package local.qoder.bridge;

import com.google.gson.*;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.*;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.*;
import java.util.concurrent.*;
import java.util.function.Consumer;

/** Authenticated loopback transport. It accepts model metadata, never provider credentials. */
public final class LocalBridge implements AutoCloseable {
    public static final String VERSION = "0.1.2";
    private static final int MAX_BODY = 128 * 1024;
    private static final Gson JSON = new Gson();
    private static final Set<String> TERMINAL = Set.of("completed", "partial", "cancelled", "failed");
    private final HttpServer server;
    private final ExecutorService workers;
    private final Consumer<Job> importer;
    private final Map<String, String> info;
    private final LinkedHashMap<String, Job> jobs = new LinkedHashMap<>();
    private final String token;
    private final String instanceId = UUID.randomUUID().toString();
    private final Path discovery;
    private volatile boolean closed;

    public record Model(String sourceId, String targetId, String displayName, Integer contextLength,
                        Boolean vision, Boolean reasoning) {}
    public record Request(String requestId, int proxyPort, List<Model> models) {}

    public static final class Job {
        public final Request request;
        private String status = "queued";
        private List<String> added = List.of(), skipped = List.of(), failed = List.of(), unselected = List.of();
        private String code = "", message = "";

        private Job(Request request) { this.request = request; }
        public synchronized boolean terminal() { return TERMINAL.contains(status); }
        public synchronized boolean cancelled() { return status.equals("cancelled"); }
        public synchronized void waiting() { if (!terminal()) status = "awaiting_user"; }
        public synchronized void saving() { if (!terminal()) status = "saving"; }
        public synchronized void finish(String state, List<String> added, List<String> skipped,
                                        List<String> failed, List<String> unselected, String code, String message) {
            if (terminal()) return;
            this.status = state;
            this.added = List.copyOf(added);
            this.skipped = List.copyOf(skipped);
            this.failed = List.copyOf(failed);
            this.unselected = List.copyOf(unselected);
            this.code = code;
            this.message = message;
        }
        public void fail(String code, String message) {
            finish("failed", List.of(), List.of(), List.of(), List.of(), code, message);
        }
        public synchronized Map<String, Object> snapshot() {
            return Map.of("request_id", request.requestId(), "status", status, "added", added,
                    "skipped", skipped, "failed", failed, "unselected", unselected,
                    "error_code", code, "message", message);
        }
    }

    public LocalBridge(Path directory, Map<String, String> info, Consumer<Job> importer) throws IOException {
        this.info = Map.copyOf(info);
        this.importer = importer;
        byte[] secret = new byte[32];
        new SecureRandom().nextBytes(secret);
        token = Base64.getUrlEncoder().withoutPadding().encodeToString(secret);
        Arrays.fill(secret, (byte) 0);
        Files.createDirectories(directory);
        secure(directory, true);
        discovery = directory.resolve("session-" + ProcessHandle.current().pid() + ".json");
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 8);
        workers = Executors.newFixedThreadPool(3, task -> {
            Thread thread = new Thread(task, "Qoder internal bridge HTTP");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(workers);
        server.createContext("/", this::handle);
        server.start();
        try {
            Map<String, Object> record = new LinkedHashMap<>(info);
            record.put("service", "qoder-internal-bridge");
            record.put("protocol", 1);
            record.put("version", VERSION);
            record.put("host", "127.0.0.1");
            record.put("port", port());
            record.put("pid", ProcessHandle.current().pid());
            record.put("instance_id", instanceId);
            record.put("token", token);
            Path temporary = Files.createTempFile(directory, ".session-", ".tmp");
            try {
                secure(temporary, false);
                Files.writeString(temporary, JSON.toJson(record), StandardCharsets.UTF_8);
                Files.move(temporary, discovery, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            } finally {
                Files.deleteIfExists(temporary);
            }
        } catch (IOException failure) {
            close();
            throw failure;
        }
    }

    public int port() { return server.getAddress().getPort(); }

    public static Path discoveryDirectory() {
        String os = System.getProperty("os.name").toLowerCase(Locale.ROOT);
        Path home = Path.of(System.getProperty("user.home"));
        if (os.contains("mac")) return home.resolve("Library/Application Support/QoderInternalBridge");
        if (os.contains("win")) {
            String local = System.getenv("LOCALAPPDATA");
            return (local == null ? home.resolve("AppData/Local") : Path.of(local)).resolve("QoderInternalBridge");
        }
        String cache = System.getenv("XDG_CACHE_HOME");
        return (cache == null ? home.resolve(".cache") : Path.of(cache)).resolve("QoderInternalBridge");
    }

    private static void secure(Path path, boolean directory) throws IOException {
        if (Files.getFileAttributeView(path, PosixFileAttributeView.class) != null) {
            Files.setPosixFilePermissions(path, PosixFilePermissions.fromString(directory ? "rwx------" : "rw-------"));
            return;
        }
        AclFileAttributeView acl = Files.getFileAttributeView(path, AclFileAttributeView.class);
        if (acl == null) throw new IOException("The session directory does not support private permissions");
        AclEntry.Builder entry = AclEntry.newBuilder().setType(AclEntryType.ALLOW)
                .setPrincipal(Files.getOwner(path)).setPermissions(EnumSet.allOf(AclEntryPermission.class));
        if (directory) entry.setFlags(AclEntryFlag.FILE_INHERIT, AclEntryFlag.DIRECTORY_INHERIT);
        acl.setAcl(List.of(entry.build()));
    }

    private void handle(HttpExchange exchange) throws IOException {
        try {
            String host = exchange.getRequestHeaders().getFirst("Host");
            String auth = exchange.getRequestHeaders().getFirst("Authorization");
            if (!exchange.getRemoteAddress().getAddress().isLoopbackAddress()
                    || !Objects.equals(host, "127.0.0.1:" + port())
                    || exchange.getRequestHeaders().containsKey("Origin")) {
                respond(exchange, 403, Map.of("error", "local_client_required"));
                return;
            }
            if (auth == null || !MessageDigest.isEqual(("Bearer " + token).getBytes(StandardCharsets.UTF_8),
                                                       auth.getBytes(StandardCharsets.UTF_8))) {
                respond(exchange, 401, Map.of("error", "bridge_auth_required"));
                return;
            }
            String path = exchange.getRequestURI().getPath();
            if (closed || exchange.getRequestURI().getRawQuery() != null) {
                respond(exchange, 400, Map.of("error", "invalid_request"));
            } else if (path.equals("/v1/health") && exchange.getRequestMethod().equals("GET")) {
                Map<String, Object> health = new LinkedHashMap<>(info);
                health.put("service", "qoder-internal-bridge");
                health.put("protocol", 1);
                health.put("version", VERSION);
                health.put("instance_id", instanceId);
                respond(exchange, 200, health);
            } else if (path.equals("/v1/imports") && exchange.getRequestMethod().equals("POST")) {
                String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
                if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
                    respond(exchange, 415, Map.of("error", "json_required"));
                    return;
                }
                byte[] body = exchange.getRequestBody().readNBytes(MAX_BODY + 1);
                if (body.length > MAX_BODY) {
                    respond(exchange, 413, Map.of("error", "request_too_large"));
                    return;
                }
                Request request;
                try { request = parse(new String(body, StandardCharsets.UTF_8)); }
                catch (RuntimeException invalid) {
                    respond(exchange, 400, Map.of("error", "invalid_models", "message", "模型请求格式无效；不能包含 Key 或地址等额外字段。"));
                    return;
                }
                Job job;
                synchronized (jobs) {
                    job = jobs.get(request.requestId());
                    if (job != null) {
                        respond(exchange, job.request.equals(request) ? 200 : 409,
                                job.request.equals(request) ? job.snapshot() : Map.of("error", "request_id_conflict"));
                        return;
                    }
                    if (jobs.values().stream().anyMatch(item -> !item.terminal())) {
                        respond(exchange, 409, Map.of("error", "import_busy", "message", "请先在 IDEA 完成或取消当前导入。"));
                        return;
                    }
                    while (jobs.size() >= 20) jobs.remove(jobs.keySet().iterator().next());
                    job = new Job(request);
                    jobs.put(request.requestId(), job);
                }
                try { importer.accept(job); }
                catch (RuntimeException failure) { job.fail("dispatch_failed", "IDEA 未能启动导入。请重新启用桥接后重试。"); }
                respond(exchange, 202, job.snapshot());
            } else if (path.startsWith("/v1/imports/") && exchange.getRequestMethod().equals("GET")) {
                String id = path.substring("/v1/imports/".length());
                Job job;
                synchronized (jobs) { job = jobs.get(id); }
                respond(exchange, job == null ? 404 : 200, job == null ? Map.of("error", "unknown_import") : job.snapshot());
            } else {
                respond(exchange, 404, Map.of("error", "unsupported_endpoint"));
            }
        } catch (RuntimeException failure) {
            respond(exchange, 500, Map.of("error", "bridge_error"));
        } finally { exchange.close(); }
    }

    public static Request parse(String body) {
        JsonObject root = JsonParser.parseString(body).getAsJsonObject();
        fields(root, Set.of("request_id", "proxy_port", "models"));
        String id = text(root, "request_id", 36);
        if (!UUID.fromString(id).toString().equals(id)) throw new IllegalArgumentException();
        int port = integer(root, "proxy_port", 1, 65535);
        JsonArray list = root.getAsJsonArray("models");
        if (list == null || list.isEmpty() || list.size() > 100) throw new IllegalArgumentException();
        List<Model> models = new ArrayList<>();
        Set<String> sources = new HashSet<>();
        for (JsonElement element : list) {
            JsonObject item = element.getAsJsonObject();
            fields(item, Set.of("source_id", "target_id", "display_name", "context_length", "vision", "reasoning"));
            String source = text(item, "source_id", 160), target = text(item, "target_id", 200);
            if (!source.matches("[A-Za-z0-9][A-Za-z0-9._:/-]*") || !target.matches("[A-Za-z0-9][A-Za-z0-9._:/-]*")
                    || !sources.add(source)) throw new IllegalArgumentException();
            String name = item.has("display_name") ? text(item, "display_name", 200) : source;
            Integer context = item.has("context_length") ? integer(item, "context_length", 1, 10_000_000) : null;
            models.add(new Model(source, target, name, context, bool(item, "vision"), bool(item, "reasoning")));
        }
        return new Request(id, port, List.copyOf(models));
    }

    private static void fields(JsonObject object, Set<String> allowed) {
        if (!allowed.containsAll(object.keySet())) throw new IllegalArgumentException();
    }
    private static String text(JsonObject item, String key, int maximum) {
        JsonPrimitive value = item.getAsJsonPrimitive(key);
        if (value == null || !value.isString()) throw new IllegalArgumentException();
        String text = value.getAsString();
        if (text.isBlank() || text.length() > maximum || text.codePoints().anyMatch(Character::isISOControl))
            throw new IllegalArgumentException();
        return text;
    }
    private static int integer(JsonObject item, String key, int minimum, int maximum) {
        JsonPrimitive value = item.getAsJsonPrimitive(key);
        if (value == null || !value.isNumber()) throw new IllegalArgumentException();
        int number = value.getAsBigDecimal().intValueExact();
        if (number < minimum || number > maximum) throw new IllegalArgumentException();
        return number;
    }
    private static Boolean bool(JsonObject item, String key) {
        if (!item.has(key)) return null;
        JsonPrimitive value = item.getAsJsonPrimitive(key);
        if (value == null || !value.isBoolean()) throw new IllegalArgumentException();
        return value.getAsBoolean();
    }
    private static void respond(HttpExchange exchange, int status, Object value) throws IOException {
        byte[] bytes = JSON.toJson(value).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.getResponseHeaders().set("Cache-Control", "no-store");
        exchange.getResponseHeaders().set("X-Content-Type-Options", "nosniff");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
    }

    @Override public void close() {
        closed = true;
        synchronized (jobs) {
            for (Job job : jobs.values()) job.finish("cancelled", List.of(), List.of(), List.of(), List.of(),
                    "bridge_stopped", "桥接已停止；未完成的导入已取消。");
        }
        server.stop(0);
        workers.shutdownNow();
        try {
            if (Files.exists(discovery)) {
                JsonObject record = JsonParser.parseString(Files.readString(discovery)).getAsJsonObject();
                if (instanceId.equals(record.get("instance_id").getAsString())) Files.delete(discovery);
            }
        } catch (IOException | RuntimeException ignored) { /* Stale records are rejected by the health check. */ }
    }
}

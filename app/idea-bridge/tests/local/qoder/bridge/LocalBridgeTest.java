package local.qoder.bridge;

import com.google.gson.*;
import java.net.URI;
import java.net.http.*;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermission;
import java.util.*;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

/** Exercises the actual loopback server without an IDE, user credentials, or model calls. */
public final class LocalBridgeTest {
    public static void main(String[] args) throws Exception {
        Path temp = Files.createTempDirectory("qoder-bridge-test-");
        AtomicInteger imports = new AtomicInteger();
        AtomicReference<LocalBridge.Job> pending = new AtomicReference<>();
        Path discovery;
        try (LocalBridge bridge = new LocalBridge(temp, Map.of("ide_name", "Test IDE"), job -> {
            imports.incrementAndGet(); pending.set(job);
        })) {
            discovery = temp.resolve("session-" + ProcessHandle.current().pid() + ".json");
            JsonObject record = JsonParser.parseString(Files.readString(discovery)).getAsJsonObject();
            String token = record.get("token").getAsString();
            HttpClient client = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build();
            String base = "http://127.0.0.1:" + bridge.port();
            check(send(client, base, "/v1/health", null, null, false).statusCode() == 401, "missing auth");
            check(send(client, base, "/v1/health", "wrong", null, false).statusCode() == 401, "wrong auth");
            var health = send(client, base, "/v1/health", token, null, false);
            check(health.statusCode() == 200 && !health.body().contains(token), "health must not disclose token");
            check(send(client, base, "/v1/health", token, null, true).statusCode() == 403, "browser origin");
            String id = UUID.randomUUID().toString();
            String body = "{\"request_id\":\"" + id + "\",\"proxy_port\":8899,\"models\":[{\"source_id\":\"fixture-model\",\"target_id\":\"qd/fixture\",\"vision\":false,\"context_length\":200000}]}";
            check(send(client, base, "/v1/imports", token, body.replace("\"vision\":false", "\"api_key\":\"must-not-be-accepted\""), false).statusCode() == 400, "credential rejected");
            check(send(client, base, "/v1/imports", token, "x".repeat(131073), false).statusCode() == 413, "body limit");
            check(send(client, base, "/v1/imports", token, body, false).statusCode() == 202, "dispatch");
            check(imports.get() == 1, "one dispatch");
            check(send(client, base, "/v1/imports", token, body, false).statusCode() == 200 && imports.get() == 1, "idempotent replay");
            check(send(client, base, "/v1/imports", token, body.replace("fixture-model", "different-model"), false).statusCode() == 409, "request id conflict");
            check(send(client, base, "/v1/imports", token, body.replace(id, UUID.randomUUID().toString()), false).statusCode() == 409, "one active import");
            pending.get().waiting();
            check(send(client, base, "/v1/imports/" + id, token, null, false).body().contains("awaiting_user"), "pending state");
            pending.get().finish("completed", List.of("fixture-model"), List.of(), List.of(), List.of(), "", "done");
            check(send(client, base, "/v1/imports/" + id, token, null, false).body().contains("completed"), "result state");
            check(send(client, base, "/v1/imports", token, body.replace(id, UUID.randomUUID().toString()), false).statusCode() == 202, "next import allowed");
            for (String invalid : List.of(body.replace("200000", "1.5"), body.replace("8899", "0"), body.replace("false", "\"false\""),
                    body.replace("fixture-model", "bad model"), body.replace("\"models\":[", "\"unknown\":1,\"models\":["))) {
                boolean rejected = false;
                try { LocalBridge.parse(invalid); } catch (RuntimeException expected) { rejected = true; }
                check(rejected, "invalid input rejected");
            }
            if (Files.getFileStore(discovery).supportsFileAttributeView("posix")) {
                check(Files.getPosixFilePermissions(discovery).equals(Set.of(PosixFilePermission.OWNER_READ, PosixFilePermission.OWNER_WRITE)), "private discovery permissions");
            }
        }
        check(!Files.exists(temp.resolve("session-" + ProcessHandle.current().pid() + ".json")), "discovery removed on close");
        Files.delete(temp);
        System.out.println("PASS: authenticated loopback, CSRF guard, input limits, credential rejection, idempotency, concurrency, status, and cleanup");
    }

    private static HttpResponse<String> send(HttpClient client, String base, String path, String token, String body, boolean origin) throws Exception {
        HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(base + path));
        if (token != null) request.header("Authorization", "Bearer " + token);
        if (origin) request.header("Origin", "https://untrusted.invalid");
        if (body != null) request.header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(body));
        return client.send(request.build(), HttpResponse.BodyHandlers.ofString());
    }
    private static void check(boolean condition, String label) {
        if (!condition) throw new AssertionError(label);
    }
}

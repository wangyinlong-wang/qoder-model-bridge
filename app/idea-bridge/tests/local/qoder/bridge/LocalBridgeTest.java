package local.qoder.bridge;

import com.google.gson.*;
import com.alibabacloud.intellij.qoder.common.ByokModelConfig;
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
        checkVisionMetadataUpdate();
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
            check(JsonParser.parseString(health.body()).getAsJsonObject().get("supports_update_vision").getAsBoolean(),
                    "vision sync capability is advertised");
            check(send(client, base, "/v1/health", token, null, true).statusCode() == 403, "browser origin");
            String id = UUID.randomUUID().toString();
            String body = "{\"request_id\":\"" + id + "\",\"proxy_port\":8899,\"models\":[{\"source_id\":\"fixture-model\",\"target_id\":\"qd/fixture\",\"vision\":false,\"context_length\":200000}]}";
            check(!LocalBridge.parse(body).updateExistingVision(), "legacy imports do not update existing models");
            String sync = body.replace("\"request_id\":", "\"update_existing_vision\":true,\"request_id\":");
            check(LocalBridge.parse(sync).updateExistingVision(), "vision updates require explicit boolean opt-in");
            check(send(client, base, "/v1/imports", token, body.replace("\"vision\":false", "\"api_key\":\"must-not-be-accepted\""), false).statusCode() == 400, "credential rejected");
            check(send(client, base, "/v1/imports", token, "x".repeat(131073), false).statusCode() == 413, "body limit");
            check(send(client, base, "/v1/imports", token, body, false).statusCode() == 202, "dispatch");
            check(imports.get() == 1, "one dispatch");
            check(send(client, base, "/v1/imports", token, body, false).statusCode() == 200 && imports.get() == 1, "idempotent replay");
            check(send(client, base, "/v1/imports", token, sync, false).statusCode() == 409, "opt-in change conflicts with the same request id");
            check(send(client, base, "/v1/imports", token, body.replace("fixture-model", "different-model"), false).statusCode() == 409, "request id conflict");
            check(send(client, base, "/v1/imports", token, body.replace(id, UUID.randomUUID().toString()), false).statusCode() == 409, "one active import");
            pending.get().waiting();
            check(send(client, base, "/v1/imports/" + id, token, null, false).body().contains("awaiting_user"), "pending state");
            pending.get().setUpdated(List.of("existing-fixture"));
            pending.get().finish("completed", List.of("fixture-model"), List.of(), List.of(), List.of(), "", "done");
            check(send(client, base, "/v1/imports/" + id, token, null, false).body().contains("completed"), "result state");
            check(pending.get().snapshot().get("updated").equals(List.of("existing-fixture")), "updated results are explicit");
            check(send(client, base, "/v1/imports", token, body.replace(id, UUID.randomUUID().toString()), false).statusCode() == 202, "next import allowed");
            for (String invalid : List.of(body.replace("200000", "1.5"), body.replace("8899", "0"), body.replace("false", "\"false\""),
                    body.replace("fixture-model", "bad model"), body.replace("\"models\":[", "\"unknown\":1,\"models\":["),
                    sync.replace("\"update_existing_vision\":true", "\"update_existing_vision\":\"true\""))) {
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
        System.out.println("PASS: native vision-only updates, explicit opt-in, authenticated loopback, CSRF guard, input limits, credential rejection, idempotency, concurrency, status, and cleanup");
    }

    private static void checkVisionMetadataUpdate() {
        Gson json = new GsonBuilder().serializeNulls().create();
        ByokModelConfig original = json.fromJson(
                "{\"customModelId\":\"preserved-credential-reference\",\"providerKey\":\"openrouter\","
                + "\"byokTypeKey\":\"pg\",\"modelKey\":\"fixture-model\",\"modelDisplayName\":\"Keep name\","
                + "\"modelDisplayNameEn\":null,\"modelDisplayNameZh\":null,"
                + "\"maxInputTokens\":12345,\"sensitiveFieldKeys\":\"api_key\","
                + "\"contextConfigJson\":\"{\\\"keep\\\":true}\"}", ByokModelConfig.class);
        original.setVl(false);
        original.setReasoning(false);
        ByokModelConfig other = json.fromJson(json.toJson(original), ByokModelConfig.class);
        other.setProviderKey("kimi");
        other.setByokTypeKey("cp");
        String before = json.toJson(original), otherBefore = json.toJson(other);
        var desired = new LocalBridge.Model("fixture-model", "upstream/model", "Do not rename", 7, true, true);
        var expectedIds = Map.of("fixture-model", original.getCustomModelId());
        List<String> updated = new ArrayList<>(), failed = new ArrayList<>();
        var result = QoderImport.applyVisionUpdates(List.of(original, other), List.of(desired), expectedIds, updated, failed);
        check(updated.equals(List.of("fixture-model")) && failed.isEmpty(), "only mapped OpenRouter record updated");
        check(result.get(0) != original && Boolean.TRUE.equals(result.get(0).isVl()), "replace metadata with a changed copy");
        check(json.toJson(original).equals(before), "original snapshot is unchanged");
        check(result.get(1) == other && json.toJson(other).equals(otherBefore), "unrelated provider remains untouched");
        ByokModelConfig expected = json.fromJson(before, ByokModelConfig.class);
        expected.setVl(true);
        check(json.toJson(result.get(0)).equals(json.toJson(expected)), "all metadata and credential references except vision preserved");
        updated.clear();
        var repeated = QoderImport.applyVisionUpdates(result, List.of(desired), expectedIds, updated, failed);
        check(updated.isEmpty() && repeated.get(0) == result.get(0), "idempotent capability sync");
        var disable = new LocalBridge.Model("fixture-model", "upstream/model", "ignored", null, false, null);
        result = QoderImport.applyVisionUpdates(result, List.of(disable), expectedIds, updated, failed);
        check(Boolean.FALSE.equals(result.get(0).isVl()), "explicit false is respected");
        updated.clear();
        var unknown = new LocalBridge.Model("fixture-model", "upstream/model", "ignored", null, null, null);
        QoderImport.applyVisionUpdates(result, List.of(unknown), expectedIds, updated, failed);
        check(updated.isEmpty(), "unknown metadata never changes an existing capability");
        var duplicate = QoderImport.applyVisionUpdates(List.of(original, original), List.of(desired), expectedIds, updated, failed);
        check(updated.isEmpty() && failed.equals(List.of("fixture-model")), "duplicate identities are not guessed");
        check(duplicate.get(0) == original && duplicate.get(1) == original, "ambiguous records remain intact");
        failed.clear();
        ByokModelConfig replacement = json.fromJson(before, ByokModelConfig.class);
        replacement.setCustomModelId("replacement-credential-reference");
        var changed = QoderImport.applyVisionUpdates(List.of(replacement), List.of(desired), expectedIds, updated, failed);
        check(updated.isEmpty() && failed.equals(List.of("fixture-model")), "replaced model identities are not overwritten");
        check(changed.get(0) == replacement && Boolean.FALSE.equals(replacement.isVl()), "replacement stays intact");
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

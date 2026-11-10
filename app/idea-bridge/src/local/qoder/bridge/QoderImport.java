package local.qoder.bridge;

import com.alibabacloud.intellij.qoder.common.ByokModelConfig;
import com.alibabacloud.intellij.qoder.core.Cosy;
import com.alibabacloud.intellij.qoder.core.lsp.LanguageWebSocketService;
import com.alibabacloud.intellij.qoder.core.lsp.result.*;
import com.alibabacloud.intellij.qoder.product.ProductCapability;
import com.alibabacloud.intellij.qoder.product.ProductRuntime;
import com.alibabacloud.intellij.qoder.service.ApplicationServices;
import com.alibabacloud.intellij.qoder.service.ByokConfigService;
import com.alibabacloud.intellij.qoder.ui.components.model.AddModelDialog;
import com.alibabacloud.intellij.qoder.ui.components.model.CustomModelItem;
import com.alibabacloud.intellij.qoder.ui.config.profile.ModelSettingsAccessPolicy;
import com.alibabacloud.intellij.qoder.util.ByokConfigUtil;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.application.ModalityState;
import com.intellij.openapi.application.WriteAction;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.project.ProjectManager;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.wm.IdeFocusManager;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import kotlin.Pair;
import javax.swing.JCheckBox;
import javax.swing.JComboBox;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;

/** Version-specific native import; existing vision metadata changes only with explicit opt-in. */
public final class QoderImport implements AutoCloseable {
    public static final Set<String> SUPPORTED_QODER = Set.of("2026.924.75731882", "2026.1014.79712739", "2026.1105.86773115");
    private static final Gson JSON = new GsonBuilder().serializeNulls().create();
    private final AtomicReference<AddModelDialog> activeDialog = new AtomicReference<>();
    private volatile boolean closing;

    public void run(LocalBridge.Job job) {
        List<String> skipped = new ArrayList<>();
        String stage = "project";
        try {
            AtomicReference<Project> projectRef = new AtomicReference<>();
            onUi(() -> {
                var frame = IdeFocusManager.getGlobalInstance().getLastFocusedFrame();
                if (frame != null) projectRef.set(frame.getProject());
                if (projectRef.get() == null) {
                    for (Project project : ProjectManager.getInstance().getOpenProjects()) {
                        if (!project.isDisposed()) { projectRef.set(project); break; }
                    }
                }
            });
            Project project = projectRef.get();
            if (project == null || project.isDisposed()) { job.fail("project_required", "请先在 IDEA 打开项目和 Qoder。"); return; }
            LanguageWebSocketService language = Cosy.INSTANCE.getLanguageService(project);
            if (language == null) { job.fail("qoder_not_ready", "请先打开项目中的 Qoder 面板。"); return; }
            stage = "access";
            var access = ModelSettingsAccessPolicy.INSTANCE.resolve(language.authStatus(5000L), language.queryUserPlan(),
                    ProductRuntime.hasCapability(ProductCapability.PERSONAL_BYOK),
                    ProductRuntime.hasCapability(ProductCapability.ENTERPRISE_BYOK));
            if (!access.getLoggedIn() || !access.getCustomModelSupported()) {
                job.fail("byok_unavailable", "当前 Qoder 账号或产品策略未开放自定义模型。"); return;
            }
            if (!proxyReady(language, job.request.proxyPort())) {
                job.fail("proxy_not_ready", "Qoder 未连接到脚本的本机转发端口，请先启动转发。"); return;
            }
            stage = "catalog";
            var fetched = ByokConfigService.INSTANCE.fetchLatestByokConfig(project);
            ByokConfigResult catalog = fetched.getConfig();
            ByokProvider provider = catalog == null || catalog.getProviders() == null ? null : catalog.getProviders().stream()
                    .filter(item -> "openrouter".equals(item.getKey())).findFirst().orElse(null);
            if (provider == null || !ByokConfigUtil.providerSelectable(provider)
                    || !ByokConfigUtil.supportsFreeFormModelName(provider)) {
                job.fail("openrouter_unavailable", "当前 Qoder 目录未开放 OpenRouter 自定义模型。"); return;
            }
            ByokProviderType type = provider.getTypes() == null ? null : provider.getTypes().stream()
                    .filter(item -> "pg".equals(item.getKey())).findFirst().orElse(null);
            if (type == null) { job.fail("type_unavailable", "当前目录没有 OpenRouter 按量付费类型。"); return; }

            List<LocalBridge.Model> pending = new ArrayList<>();
            List<LocalBridge.Model> existingRequests = new ArrayList<>();
            Map<String, String> expectedIds = new HashMap<>();
            onUi(() -> {
                List<ByokModelConfig> existing = existing();
                for (LocalBridge.Model model : job.request.models()) {
                    var matches = existing.stream().filter(item -> "openrouter".equals(item.getProviderKey())
                            && model.sourceId().equals(item.getModelKey())).toList();
                    if (matches.isEmpty()) pending.add(model);
                    else {
                        skipped.add(model.sourceId());
                        existingRequests.add(model);
                        if (matches.size() == 1 && matches.get(0).getCustomModelId() != null)
                            expectedIds.put(model.sourceId(), matches.get(0).getCustomModelId());
                    }
                }
            });
            if (pending.isEmpty()) {
                if (closing || job.cancelled()) return;
                List<String> updated = new ArrayList<>(), failed = new ArrayList<>();
                if (job.request.updateExistingVision()) {
                    stage = "vision";
                    job.saving();
                    onUi(() -> WriteAction.run(() -> {
                        if (closing || job.cancelled()) return;
                        var state = ApplicationServices.persistentSetting().getState();
                        if (state == null) throw new IllegalStateException("Settings unavailable");
                        var all = applyVisionUpdates(existing(), existingRequests, expectedIds, updated, failed);
                        if (!updated.isEmpty()) state.setByokModelConfigs(all);
                    }));
                }
                skipped.removeAll(updated);
                skipped.removeAll(failed);
                job.setUpdated(updated);
                job.finish(failed.isEmpty() ? "completed" : "partial", List.of(), skipped, failed, List.of(),
                        failed.isEmpty() ? "" : "vision_update_failed",
                        updated.isEmpty() && failed.isEmpty() ? "所选模型已存在，图片能力未改变，凭据保持原样。"
                                : "图片能力同步完成；请重新选择模型或新建会话。无法安全匹配的模型未修改，凭据保持原样。");
                return;
            }
            if (closing || job.cancelled()) return;
            ByokConfigResult scoped = scopedCatalog(provider, type, pending);
            Set<String> wanted = new LinkedHashSet<>();
            pending.forEach(model -> wanted.add(model.sourceId()));
            AtomicReference<List<CustomModelItem>> result = new AtomicReference<>();
            AtomicReference<String> dialogFailure = new AtomicReference<>();
            stage = "dialog";
            job.waiting();
            onUi(() -> {
                if (closing || job.cancelled()) return;
                AddModelDialog dialog = new AddModelDialog(scoped, List.of(), null);
                activeDialog.set(dialog);
                try {
                    preselect(dialog, wanted);
                    dialog.setTitle("导入脚本选中的 Qoder 模型（仅新增）");
                    if (dialog.showAndGet()) result.set(new ArrayList<>(dialog.getResultItems()));
                } catch (ReflectiveOperationException failure) {
                    dialogFailure.set("当前 Qoder 添加窗口结构与已适配版本不一致。");
                    dialog.close(DialogWrapper.CANCEL_EXIT_CODE);
                } finally {
                    activeDialog.compareAndSet(dialog, null);
                }
            });
            if (dialogFailure.get() != null) { job.fail("dialog_incompatible", dialogFailure.get()); return; }
            List<CustomModelItem> submitted = result.getAndSet(null);
            if (submitted == null || closing || job.cancelled()) {
                job.finish("cancelled", List.of(), skipped, List.of(), List.of(), "", "用户取消导入，没有新增模型。");
                return;
            }
            Set<String> returned = new HashSet<>();
            for (CustomModelItem item : submitted) {
                if (!"openrouter".equals(item.getProviderKey()) || !"pg".equals(item.getByokTypeKey())
                        || !wanted.contains(item.getModelKey()) || !returned.add(item.getModelKey())
                        || item.getCustomModelId().isBlank()) {
                    job.fail("selection_changed", "导入范围发生变化，请仅选择脚本提交的模型。"); return;
                }
            }
            if (!proxyReady(language, job.request.proxyPort())) {
                job.fail("proxy_changed", "Qoder 代理已变化，未保存本次模型。请恢复转发后重试。"); return;
            }
            List<String> unselected = wanted.stream().filter(id -> !returned.contains(id)).toList();
            List<CustomModelItem> fresh = new ArrayList<>();
            onUi(() -> {
                List<ByokModelConfig> existing = existing();
                Set<String> ids = new HashSet<>();
                existing.forEach(item -> ids.add(item.getCustomModelId()));
                for (CustomModelItem item : submitted) {
                    if (contains(existing, item.getModelKey())) skipped.add(item.getModelKey());
                    else if (!ids.contains(item.getCustomModelId())) fresh.add(item);
                }
            });
            job.saving();
            stage = "credentials";
            List<CustomModelItem> verified = new ArrayList<>();
            List<String> failed = new ArrayList<>();
            for (CustomModelItem item : fresh) {
                if (closing || job.cancelled()) return;
                // Only new items from the user-confirmed Qoder dialog enter PasswordSafe.
                if (CustomModelItem.Companion.saveAndVerifySensitiveFields(List.of(item)).isEmpty()) verified.add(item);
                else failed.add(item.getModelKey());
            }
            List<ByokModelConfig> additions = CustomModelItem.Companion.toByokModelConfigsWithoutSavingSensitiveFields(verified);
            List<String> added = new ArrayList<>();
            List<String> updated = new ArrayList<>(), visionFailures = new ArrayList<>();
            stage = "settings";
            onUi(() -> WriteAction.run(() -> {
                if (closing || job.cancelled()) return;
                var state = ApplicationServices.persistentSetting().getState();
                if (state == null) throw new IllegalStateException("Settings unavailable");
                List<ByokModelConfig> all = new ArrayList<>(existing());
                if (job.request.updateExistingVision())
                    all = applyVisionUpdates(all, existingRequests, expectedIds, updated, visionFailures);
                for (ByokModelConfig item : additions) {
                    if (contains(all, item.getModelKey())) skipped.add(item.getModelKey());
                    else { all.add(item); added.add(item.getModelKey()); }
                }
                // PasswordSafe is not involved in capability-only changes.
                if (!added.isEmpty() || !updated.isEmpty()) state.setByokModelConfigs(all);
            }));
            skipped.removeAll(updated);
            skipped.removeAll(visionFailures);
            failed.addAll(visionFailures);
            job.setUpdated(updated);
            job.finish(failed.isEmpty() ? "completed" : "partial", added, skipped, failed, unselected,
                    failed.isEmpty() ? "" : visionFailures.isEmpty() ? "credential_save_failed" : "vision_update_failed",
                    failed.isEmpty() ? "导入/同步完成，请在 Qoder 中重新选择 (byok) 模型或新建会话。"
                            : "部分模型未新增或未同步；请核对重复 ID、供应商类型及凭据权限。已成功的模型保留。");
        } catch (Exception | LinkageError failure) {
            // Exceptions from native dialogs/storage can contain values supplied by the user. Do not log them.
            job.fail("native_" + stage + "_failed", "Qoder 导入未完成。请确认插件版本、登录状态及凭据保存权限后重试。");
        }
    }

    static List<ByokModelConfig> applyVisionUpdates(List<ByokModelConfig> existing, List<LocalBridge.Model> requested,
                                                   Map<String, String> expectedIds, List<String> updated, List<String> failed) {
        List<ByokModelConfig> result = new ArrayList<>(existing);
        for (LocalBridge.Model spec : requested) {
            if (spec.vision() == null) continue;
            List<Integer> matches = new ArrayList<>();
            for (int index = 0; index < existing.size(); index++) {
                ByokModelConfig model = existing.get(index);
                if ("openrouter".equals(model.getProviderKey()) && spec.sourceId().equals(model.getModelKey()))
                    matches.add(index);
            }
            if (matches.size() != 1) {
                failed.add(spec.sourceId());
                continue;
            }
            ByokModelConfig model = existing.get(matches.get(0));
            String expected = expectedIds.get(spec.sourceId());
            if (expected == null || expected.isBlank() || !expected.equals(model.getCustomModelId())
                    || !"pg".equals(model.getByokTypeKey())) {
                failed.add(spec.sourceId());
                continue;
            }
            if (Objects.equals(model.isVl(), spec.vision())) continue;
            // Clone the complete native metadata, including fields added by newer plugin versions.
            // Keep the saved object and every credential reference intact until the UI-thread write.
            ByokModelConfig copy = JSON.fromJson(JSON.toJson(model), ByokModelConfig.class);
            copy.setVl(spec.vision());
            result.set(matches.get(0), copy);
            updated.add(spec.sourceId());
        }
        return result;
    }

    private static boolean proxyReady(LanguageWebSocketService language, int port) {
        try {
            var config = language.getGlobalConfig(5000L);
            if (config == null || !"manual".equals(config.getProxyMode())
                    || !("http://127.0.0.1:" + port).equals(config.getHttpProxy())) return false;
            try (Socket socket = new Socket()) { socket.connect(new InetSocketAddress("127.0.0.1", port), 1500); }
            return true;
        } catch (Exception failure) { return false; }
    }

    private static List<ByokModelConfig> existing() {
        var state = ApplicationServices.persistentSetting().getState();
        if (state == null) throw new IllegalStateException("Settings unavailable");
        return state.getByokModelConfigs() == null ? List.of() : new ArrayList<>(state.getByokModelConfigs());
    }
    private static boolean contains(List<ByokModelConfig> models, String source) {
        return models.stream().anyMatch(item -> "openrouter".equals(item.getProviderKey()) && source.equals(item.getModelKey()));
    }

    private static ByokConfigResult scopedCatalog(ByokProvider original, ByokProviderType originalType,
                                                  List<LocalBridge.Model> selected) {
        List<ByokModel> models = new ArrayList<>();
        for (LocalBridge.Model spec : selected) {
            ByokModel model = ByokConfigUtil.createFreeFormModel(spec.sourceId());
            model.setDisplayName(spec.displayName());
            model.setDescription("本机映射：" + spec.sourceId() + " → " + spec.targetId());
            if (spec.contextLength() != null) model.setMaxInputTokens(spec.contextLength());
            if (spec.vision() != null) model.setVl(spec.vision());
            if (spec.reasoning() != null) model.setReasoning(spec.reasoning());
            models.add(model);
        }
        ByokProviderType type = new ByokProviderType();
        type.setKey(originalType.getKey());
        type.setDisplayName(originalType.getDisplayName());
        type.setApiKeyUrl(originalType.getApiKeyUrl());
        type.setModels(models);
        ByokProvider provider = new ByokProvider();
        provider.setKey(original.getKey());
        provider.setEnabled(original.getEnabled());
        provider.setSource(original.getSource());
        provider.setDisplayName(original.getDisplayName());
        provider.setApiKeyUrl(original.getApiKeyUrl());
        provider.setFields(original.getFields());
        provider.setTypes(List.of(type));
        provider.setModels(List.of());
        ByokConfigResult result = new ByokConfigResult();
        result.setProviders(List.of(provider));
        return result;
    }

    private static void preselect(AddModelDialog dialog, Set<String> selected) throws ReflectiveOperationException {
        // Qoder initially selects placeholder rows. Its normal listeners must build the model list first.
        selectNativeChoice(dialog, "providerComboBox", "getProvider", "openrouter");
        selectNativeChoice(dialog, "typeComboBox", "getProviderType", "pg");
        Field field = AddModelDialog.class.getDeclaredField("modelCheckBoxes");
        field.setAccessible(true);
        int count = 0;
        for (Object entry : (List<?>) field.get(dialog)) {
            Pair<?, ?> pair = (Pair<?, ?>) entry;
            JCheckBox checkBox = (JCheckBox) pair.getFirst();
            ByokModel model = (ByokModel) pair.getSecond();
            if (selected.contains(model.getKey()) && checkBox.isEnabled()) { checkBox.setSelected(true); count++; }
        }
        if (count != selected.size()) throw new ReflectiveOperationException("Native model selection differs");
    }

    private static void selectNativeChoice(AddModelDialog dialog, String fieldName, String accessor,
                                           String expectedKey) throws ReflectiveOperationException {
        Field field = AddModelDialog.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        JComboBox<?> choices = (JComboBox<?>) field.get(dialog);
        for (int index = 0; index < choices.getItemCount(); index++) {
            Object item = choices.getItemAt(index);
            if (item == null) continue;
            Method getter = item.getClass().getDeclaredMethod(accessor);
            getter.setAccessible(true);
            Object value = getter.invoke(item);
            String key = value instanceof ByokProvider provider ? provider.getKey()
                    : value instanceof ByokProviderType type ? type.getKey() : null;
            if (expectedKey.equals(key)) {
                choices.setSelectedIndex(index);
                return;
            }
        }
        throw new ReflectiveOperationException("Native provider or type selection differs");
    }
    private static void onUi(Runnable operation) {
        ApplicationManager.getApplication().invokeAndWait(operation, ModalityState.any());
    }

    @Override public void close() {
        closing = true;
        AddModelDialog dialog = activeDialog.getAndSet(null);
        if (dialog != null) ApplicationManager.getApplication().invokeLater(
                () -> dialog.close(DialogWrapper.CANCEL_EXIT_CODE), ModalityState.any());
    }
}

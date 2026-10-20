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
import kotlin.Pair;
import javax.swing.JCheckBox;
import javax.swing.JComboBox;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;

/** The only Qoder-version-specific code. Existing models and their credentials are never changed. */
public final class QoderImport implements AutoCloseable {
    public static final Set<String> SUPPORTED_QODER = Set.of("2026.924.75731882", "2026.1014.79712739");
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
            onUi(() -> {
                List<ByokModelConfig> existing = existing();
                for (LocalBridge.Model model : job.request.models()) {
                    if (contains(existing, model.sourceId())) skipped.add(model.sourceId());
                    else pending.add(model);
                }
            });
            if (pending.isEmpty()) {
                job.finish("completed", List.of(), skipped, List.of(), List.of(), "", "所选模型已存在，没有改写原配置或凭据。");
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
            stage = "settings";
            onUi(() -> WriteAction.run(() -> {
                if (closing || job.cancelled()) return;
                var state = ApplicationServices.persistentSetting().getState();
                if (state == null) throw new IllegalStateException("Settings unavailable");
                List<ByokModelConfig> all = new ArrayList<>(existing());
                for (ByokModelConfig item : additions) {
                    if (contains(all, item.getModelKey())) skipped.add(item.getModelKey());
                    else { all.add(item); added.add(item.getModelKey()); }
                }
                // Append the new entries; retain every existing object and credential reference unchanged.
                state.setByokModelConfigs(all);
            }));
            job.finish(failed.isEmpty() ? "completed" : "partial", added, skipped, failed, unselected,
                    failed.isEmpty() ? "" : "credential_save_failed",
                    failed.isEmpty() ? "导入完成，请在 Qoder 模型选择器中选择新增的 (byok) 模型。"
                            : "部分凭据保存或读取验证失败，这些模型没有加入清单。已成功的模型保留。");
        } catch (Exception | LinkageError failure) {
            // Exceptions from native dialogs/storage can contain values supplied by the user. Do not log them.
            job.fail("native_" + stage + "_failed", "Qoder 导入未完成。请确认插件版本、登录状态及凭据保存权限后重试。");
        }
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

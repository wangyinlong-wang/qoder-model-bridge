package local.qoder.bridge;

import com.intellij.ide.plugins.PluginManagerCore;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.actionSystem.*;
import com.intellij.openapi.application.*;
import com.intellij.openapi.components.Service;
import com.intellij.openapi.extensions.PluginId;
import com.intellij.openapi.diagnostic.Logger;
import com.intellij.openapi.project.DumbAwareAction;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.startup.StartupActivity;
import com.intellij.openapi.ui.Messages;
import org.jetbrains.annotations.NotNull;
import java.io.IOException;
import java.util.Map;

@Service(Service.Level.APP)
public final class BridgeService implements Disposable {
    private static final Logger LOG = Logger.getInstance(BridgeService.class);
    private LocalBridge bridge;
    private final QoderImport importer = new QoderImport();

    public static BridgeService getInstance() {
        return ApplicationManager.getApplication().getService(BridgeService.class);
    }

    public synchronized int start() throws IOException {
        if (bridge == null) {
            var qoder = PluginManagerCore.getPlugin(PluginId.getId("com.qoder"));
            if (qoder == null || !qoder.isEnabled() || !QoderImport.SUPPORTED_QODER.contains(qoder.getVersion()))
                throw new IOException("Qoder 版本不匹配。当前：" + (qoder == null ? "未安装" : qoder.getVersion())
                        + "；支持：" + String.join("、", QoderImport.SUPPORTED_QODER) + "。");
            Map<String, String> info = Map.of("ide_name", ApplicationNamesInfo.getInstance().getFullProductName(),
                    "ide_version", ApplicationInfo.getInstance().getFullVersion(),
                    "qoder_version", qoder.getVersion());
            bridge = new LocalBridge(LocalBridge.discoveryDirectory(), info,
                    job -> ApplicationManager.getApplication().executeOnPooledThread(() -> importer.run(job)));
        }
        return bridge.port();
    }

    @Override public synchronized void dispose() {
        importer.close();
        if (bridge != null) { bridge.close(); bridge = null; }
    }

    public static final class Startup implements StartupActivity.DumbAware {
        @Override public void runActivity(@NotNull Project project) {
            ApplicationManager.getApplication().executeOnPooledThread(() -> {
                try { getInstance().start(); }
                catch (IOException failure) { LOG.warn("Qoder bridge startup failed: " + failure.getMessage()); }
            });
        }
    }

    public static final class EnableAction extends DumbAwareAction {
        @Override public @NotNull ActionUpdateThread getActionUpdateThread() { return ActionUpdateThread.BGT; }
        @Override public void actionPerformed(@NotNull AnActionEvent event) {
            ApplicationManager.getApplication().executeOnPooledThread(() -> {
                String message;
                boolean success;
                try {
                    int port = getInstance().start();
                    message = "桥接已监听 127.0.0.1:" + port + "。\n在脚本中勾选模型、启动转发，再点击“一键添加到 IDEA / Qoder”。\n导入时会打开 Qoder 原生窗口，由你输入凭据并确认。";
                    success = true;
                } catch (IOException failure) {
                    message = "桥接未启动：" + failure.getMessage();
                    success = false;
                }
                String text = message;
                boolean ok = success;
                ApplicationManager.getApplication().invokeLater(() -> {
                    if (ok) Messages.showInfoMessage(text, "Qoder 内网模型桥接");
                    else Messages.showErrorDialog(text, "Qoder 内网模型桥接");
                });
            });
        }
    }
}

#import <AppKit/AppKit.h>
#include <Python.h>

static int failStartup(PyStatus status, int argc) {
    const char *message = status.err_msg ? status.err_msg : "Python initialization failed";
    fprintf(stderr, "%s\n", message);
    if (argc == 1) {
        NSAlert *alert = [[NSAlert alloc] init];
        alert.messageText = @"Qoder Model Bridge 无法启动";
        alert.informativeText = [NSString stringWithUTF8String:message];
        [alert addButtonWithTitle:@"确定"];
        [alert runModal];
    }
    return 1;
}

int main(int argc, char *argv[]) {
    @autoreleasepool {
        NSString *contents = [[NSBundle mainBundle].bundlePath stringByAppendingPathComponent:@"Contents"];
        NSString *home = [contents stringByAppendingPathComponent:@"Resources/python"];
        NSString *entry = [contents stringByAppendingPathComponent:@"Resources/app/app_entry.py"];
        NSString *interpreter = [home stringByAppendingPathComponent:@"bin/python3.13"];
        PyPreConfig preconfig;
        PyPreConfig_InitIsolatedConfig(&preconfig);
        preconfig.utf8_mode = 1;
        PyStatus status = Py_PreInitialize(&preconfig);
        if (PyStatus_Exception(status)) return failStartup(status, argc);

        PyConfig config;
        PyConfig_InitIsolatedConfig(&config);
        config.parse_argv = 0;
        config.write_bytecode = 0;
        status = PyConfig_SetBytesString(&config, &config.home, home.fileSystemRepresentation);
        if (!PyStatus_Exception(status)) status = PyConfig_SetBytesString(&config, &config.executable, interpreter.fileSystemRepresentation);
        if (!PyStatus_Exception(status)) status = PyConfig_SetBytesString(&config, &config.run_filename, entry.fileSystemRepresentation);
        if (!PyStatus_Exception(status)) status = PyConfig_SetBytesArgv(&config, argc, argv);
        if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
        if (PyStatus_Exception(status)) {
            int result = failStartup(status, argc);
            PyConfig_Clear(&config);
            return result;
        }
        PyConfig_Clear(&config);
        return Py_RunMain();
    }
}

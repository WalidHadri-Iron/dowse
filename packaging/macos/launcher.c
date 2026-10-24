/* Dowse.app's program (Contents/MacOS/Dowse): the Python inside the app (Contents/Resources/python), embedded.
 *
 * macOS names a process, its menu and its Dock icon after the bundle its program is in, so the program has to be
 * the app's own: python exec'd from here would be "Python" again. Opened from Finder, Launchpad or at login (no
 * arguments, or Dowse's own: --background, --library, --quit…), it runs the app (`-m dowse.desktop.launch`). Given
 * Python's options (`-m module`, `-c code`), it is that Python: how the app starts its own processes (the window,
 * the model server's guard) with sys.executable, which is this program.
 *
 * Isolated from the Mac's own Python: PYTHON* variables and the user's site-packages are ignored.
 */

#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fail(PyConfig *config, PyStatus status) {
    PyConfig_Clear(config);
    if (PyStatus_IsExit(status)) return status.exitcode;
    Py_ExitStatusException(status);
    return 1;
}

static int python_options(int argc, char **argv) {
    /* "-m", "-c", "-I"…: Python's single-dash options. Dowse's own are double-dash; "-psn_…" was Finder's. */
    return argc > 1 && argv[1][0] == '-' && argv[1][1] != '-' && argv[1][1] != '\0' &&
           strncmp(argv[1], "-psn_", 5) != 0;
}

int main(int argc, char **argv) {
    char found[PATH_MAX], program[PATH_MAX], contents[PATH_MAX], home[PATH_MAX];
    uint32_t size = sizeof found;
    if (_NSGetExecutablePath(found, &size) != 0 || realpath(found, program) == NULL) {
        fprintf(stderr, "Dowse: can't find its own program\n");
        return 1;
    }
    strlcpy(contents, program, sizeof contents);   /* …/Dowse.app/Contents/MacOS/Dowse → …/Dowse.app/Contents */
    for (int up = 0; up < 2; up++) {
        char *slash = strrchr(contents, '/');
        if (slash == NULL || slash == contents) {
            fprintf(stderr, "Dowse: its program isn't inside Dowse.app\n");
            return 1;
        }
        *slash = '\0';
    }
    snprintf(home, sizeof home, "%s/Resources/python", contents);

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.parse_argv = 1;                /* -m, -c, as python reads them */
    config.install_signal_handlers = 1;   /* Ctrl+C, SIGPIPE: as python does */
    config.write_bytecode = 0;            /* the app never writes into itself (its signature seals it) */
    PyStatus status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) return fail(&config, status);
    status = PyConfig_SetBytesString(&config, &config.executable, program);
    if (PyStatus_Exception(status)) return fail(&config, status);

    char **args = argv;
    int count = argc;
    if (!python_options(argc, argv)) {
        count = argc + 2;
        args = calloc((size_t)count + 1, sizeof *args);
        if (args == NULL) return 1;
        args[0] = argv[0];
        args[1] = "-m";
        args[2] = "dowse.desktop.launch";
        for (int i = 1; i < argc; i++) args[i + 2] = argv[i];
    }
    status = PyConfig_SetBytesArgv(&config, count, args);
    if (PyStatus_Exception(status)) return fail(&config, status);
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) return fail(&config, status);
    PyConfig_Clear(&config);
    return Py_RunMain();
}

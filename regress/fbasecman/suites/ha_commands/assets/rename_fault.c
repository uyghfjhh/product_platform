#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

typedef int (*renameat_fn)(int, const char *, int, const char *);
typedef int (*openat_fn)(int, const char *, int, ...);
typedef int (*fsync_fn)(int);

static void inject_external_edit(void)
{
    const char *edit_path = getenv("FB_TEST_EXTERNAL_EDIT_PATH");
    const char *done = getenv("FB_TEST_EXTERNAL_EDIT_DONE");
    if (edit_path == NULL || (done != NULL && strcmp(done, "1") == 0))
        return;
    int fd = open(edit_path, O_WRONLY | O_APPEND | O_CLOEXEC);
    if (fd >= 0) {
        static const char edit[] = "# external edit injected by hook\n";
        (void)write(fd, edit, sizeof(edit) - 1);
        close(fd);
        setenv("FB_TEST_EXTERNAL_EDIT_DONE", "1", 1);
    }
}

int fsync(int fd)
{
    static fsync_fn real_fsync;
    char proc_path[64];
    char resolved[512];
    if (real_fsync == NULL)
        real_fsync = (fsync_fn)dlsym(RTLD_NEXT, "fsync");
    snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", fd);
    ssize_t length = readlink(proc_path, resolved, sizeof(resolved) - 1);
    if (length > 0) {
        resolved[length] = '\0';
    }
    if (getenv("FB_TEST_RENAME_MODE") != NULL &&
        length > 0 && strstr(resolved, "conf-backup") != NULL)
        inject_external_edit();
    return real_fsync(fd);
}

int renameat(int olddirfd, const char *oldpath, int newdirfd,
             const char *newpath)
{
    static renameat_fn real_renameat;
    const char *target = getenv("FB_TEST_RENAME_TARGET");
    const char *log_path = getenv("FB_TEST_RENAME_LOG");
    const char *edit_path = getenv("FB_TEST_EXTERNAL_EDIT_PATH");
    const char *mode = getenv("FB_TEST_RENAME_MODE");

    if (real_renameat == NULL)
        real_renameat = (renameat_fn)dlsym(RTLD_NEXT, "renameat");
    if (target != NULL && oldpath != NULL && newpath != NULL &&
        strcmp(newpath, target) == 0 && strstr(oldpath, ".tmp") != NULL) {
        if (mode != NULL && strcmp(mode, "reload-restore-failure") == 0) {
            int fd = openat(olddirfd, oldpath, O_WRONLY | O_APPEND | O_CLOEXEC);
            if (fd >= 0) {
                static const char invalid[] =
                    "\nnot_a_real_parameter \"reload fault injection\"\n";
                (void)write(fd, invalid, sizeof(invalid) - 1);
                close(fd);
            }
            if (log_path != NULL) {
                fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0600);
                if (fd >= 0) {
                    static const char message[] = "candidate corrupted after validation\n";
                    (void)write(fd, message, sizeof(message) - 1);
                    close(fd);
                }
            }
            return real_renameat(olddirfd, oldpath, newdirfd, newpath);
        }
        if (edit_path != NULL) {
            int fd = open(edit_path, O_WRONLY | O_APPEND | O_CLOEXEC);
            if (fd >= 0) {
                static const char edit[] = "# external edit injected by hook\n";
                (void)write(fd, edit, sizeof(edit) - 1);
                close(fd);
            }
        }
        if (log_path != NULL) {
            int fd = open(log_path, O_WRONLY | O_CREAT | O_APPEND, 0600);
            if (fd >= 0) {
                static const char message[] = "renameat candidate rejected\n";
                (void)write(fd, message, sizeof(message) - 1);
                close(fd);
            }
        }
        if (mode == NULL || strcmp(mode, "external-edit") != 0) {
            errno = EIO;
            return -1;
        }
    }
    return real_renameat(olddirfd, oldpath, newdirfd, newpath);
}

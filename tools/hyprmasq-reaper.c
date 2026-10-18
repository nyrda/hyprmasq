// hyprmasq-reaper: runs a command as its child and stays until the command and every process descending from it have
// exited.
//
//   hyprmasq-reaper COMMAND [ARG...]
//
// The plugin starts the command of a launch through it. As the subreaper of the command's processes, it becomes the
// parent of every one whose own parent exits, as the GUI of an app whose launcher forks it away and exits, so the
// plugin still finds the launch among the parents of each window's process. Its exit status is the command's, or 128
// plus the number of the signal that ended the command. When the kernel refuses to make it a subreaper, it runs the
// command all the same; when it cannot fork, it executes the command in its place.

#define _GNU_SOURCE

#include <errno.h>
#include <stdio.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <unistd.h>

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: hyprmasq-reaper COMMAND [ARG...]\n");
        return 2;
    }
    if (prctl(PR_SET_CHILD_SUBREAPER, 1) != 0)
        perror("hyprmasq-reaper: cannot become a subreaper");
    const pid_t child = fork();
    if (child <= 0) {
        if (child < 0)
            perror("hyprmasq-reaper: cannot fork");
        execvp(argv[1], argv + 1);
        fprintf(stderr, "hyprmasq-reaper: cannot execute %s: %m\n", argv[1]);
        _exit(127);
    }
    // Reaps the command and the orphans that the kernel gives it, until it has no child left.
    int result = 0;
    for (;;) {
        int         status = 0;
        const pid_t pid    = waitpid(-1, &status, 0);
        if (pid < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (pid == child)
            result = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    }
    return result;
}

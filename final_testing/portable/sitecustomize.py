"""Automatically remount exported sources in explicitly opted-in Python children."""

import os
import sys

if os.environ.get("TEMP_DESK_PORTABLE_ROOT") or os.environ.get("FINAL_TESTING_PORTABLE_ROOT"):
    try:
        from final_testing.portable.mount import install_from_env

        install_from_env()
        # A miniature filesystem fixture opts out of runtime admission only.
        # Real launchers strip this test marker; the source mount is mandatory
        # even for fixtures. Source/model/application bytes stay unchanged.
        if os.environ.get("TEMP_DESK_PORTABLE_UNIT_TEST") != "filesystem":
            import final_testing.portable.runtime_binding as runtime_binding

            runtime_binding.install()
    except BaseException as error:
        # Python otherwise reports a sitecustomize error and keeps executing.
        # Never allow an opted-in application to run after failed admission.
        # Emit only the error type; input/credential values are not echoed.
        sys.stderr.write("Portable bootstrap failed: " + type(error).__name__ + "\n")
        sys.stderr.flush()
        os._exit(78)

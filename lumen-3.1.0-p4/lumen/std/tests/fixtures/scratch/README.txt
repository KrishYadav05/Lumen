Scratch space for std/tests/fs_test.lum. Lumen has no mkdir or temp-dir
builtin, so the tests need a directory that already exists to write into;
they create and delete their own files here and leave nothing behind.
Run `lumen test` from the repository root (paths are cwd-relative).

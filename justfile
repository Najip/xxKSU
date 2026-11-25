alias bk := build_ksud
alias bm := build_manager

build_ksud:
    cargo build -p ksud --target aarch64-linux-android --release

build_manager: build_ksud
    mkdir -p manager/app/src/main/jniLibs/arm64-v8a
    cp target/aarch64-linux-android/release/ksud manager/app/src/main/jniLibs/arm64-v8a/libksud.so
    cd manager && ./gradlew assembleDebug

clippy:
    cargo fmt
    cargo clippy -p ksud --target aarch64-linux-android --release

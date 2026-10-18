//! Compiles the reused C11 kernels (v6/native/rs.c, v5/native/align.c, native/c/orient.c) into a static archive that
//! is linked into the engine library. Standard library only: the system C compiler is invoked directly (CC, else cc).
//! VNX_ENGINE_CFLAGS adds flags (sanitizer builds).
//! Cross builds (docs/V14_BUILD_AND_DEPLOYMENT.md): the compiler and archiver for the target come from
//! CC_<target> / AR_<target> (e.g. CC_aarch64_unknown_linux_gnu), then CC / AR, then cc / ar, so that a cross
//! build does not need a global CC that would also apply to host tools, and can use the target's own `ar`.
use std::env;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    let here = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let pkg = here.join("../../..").canonicalize().expect("vnxdna package directory");
    let sources = [
        pkg.join("v6/native/rs.c"),
        pkg.join("v5/native/align.c"),
        pkg.join("native/c/orient.c"),
    ];
    let out = PathBuf::from(env::var("OUT_DIR").unwrap());
    let target = env::var("TARGET").unwrap_or_default().replace('-', "_");
    let tool = |name: &str, default: &str| -> String {
        env::var(format!("{}_{}", name, target))
            .or_else(|_| env::var(name))
            .unwrap_or_else(|_| default.to_string())
    };
    let cc = tool("CC", "cc");
    let ar = tool("AR", "ar");
    let extra: Vec<String> = env::var("VNX_ENGINE_CFLAGS")
        .unwrap_or_default()
        .split_whitespace()
        .map(|s| s.to_string())
        .collect();
    let mut objs = Vec::new();
    for src in &sources {
        println!("cargo:rerun-if-changed={}", src.display());
        let stem = src.file_stem().unwrap().to_string_lossy().to_string();
        let obj = out.join(format!("{}.o", stem));
        let mut cmd = Command::new(&cc);
        cmd.args(["-O3", "-std=c11", "-fPIC", "-Wall", "-Wextra", "-c"]).args(&extra).arg(src).arg("-o").arg(&obj);
        let st = cmd.status().expect("C compiler");
        assert!(st.success(), "compiling {} failed", src.display());
        objs.push(obj);
    }
    println!("cargo:rerun-if-changed={}", pkg.join("v5/native/align_dp.inc").display());
    println!("cargo:rerun-if-env-changed=VNX_ENGINE_CFLAGS");
    println!("cargo:rerun-if-env-changed=CC");
    println!("cargo:rerun-if-env-changed=AR");
    println!("cargo:rerun-if-env-changed=CC_{}", target);
    println!("cargo:rerun-if-env-changed=AR_{}", target);
    let lib = out.join("libvnxkern.a");
    let _ = std::fs::remove_file(&lib);
    let st = Command::new(&ar).arg("crs").arg(&lib).args(&objs).status().expect("ar");
    assert!(st.success(), "ar failed");
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=vnxkern");
}

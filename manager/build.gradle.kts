plugins {
    alias(libs.plugins.agp.app) apply false
    alias(libs.plugins.kotlin) apply false
    alias(libs.plugins.compose.compiler) apply false
}

extra["androidMinSdkVersion"] = 25
extra["androidTargetSdkVersion"] = 37
extra["androidCompileSdkVersion"] = 37
extra["androidCompileSdkVersionMinor"] = 0
extra["androidBuildToolsVersion"] = "37.0.0"
extra["androidCompileNdkVersion"] = libs.versions.ndk.get()
extra["androidSourceCompatibility"] = JavaVersion.VERSION_21
extra["androidTargetCompatibility"] = JavaVersion.VERSION_21
extra["managerVersionCode"] = getVersionCode()
extra["managerVersionName"] = getVersionName()

fun runGit(vararg args: String): String {
    val process = ProcessBuilder(listOf("git") + args)
        .directory(rootDir)
        .start()
    val output = process.inputStream.bufferedReader().use { it.readText().trim() }
    val exitCode = process.waitFor()
    check(exitCode == 0) { "git ${args.joinToString(" ")} failed with exit code $exitCode" }
    return output
}

fun getGitCommitCount(): Int {
    val count = runGit("rev-list", "--count", "HEAD").toInt()
    check(count >= 100) { "Shallow clone detected (commit count=$count); fetch full git history (fetch-depth: 0)." }
    return count
}

fun getGitDescribe(): String {
    val desc = runGit("describe", "--tags", "--match", "v*", "--always")
    check(desc.startsWith("v")) { "git describe returned '$desc' without v* tag; fetch tags (fetch-depth: 0)." }
    return desc
}

fun getVersionCode(): Int {
    val commitCount = getGitCommitCount()
    return 30000 + commitCount - 92
}

fun getVersionName(): String {
    return getGitDescribe()
}

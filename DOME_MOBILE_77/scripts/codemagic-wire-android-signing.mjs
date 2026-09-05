/**
 * Patches Expo SDK 57's CI-generated Android app Gradle file so only a
 * Codemagic-provided release keystore signs the release variant.
 *
 * This script is intentionally fail-closed: a changed Expo template stops the
 * build rather than silently producing an APK signed with debug.keystore.
 * It never reads, prints, stores, or commits private-key material.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const required = [
  'CM_KEYSTORE_PATH',
  'CM_KEYSTORE_PASSWORD',
  'CM_KEY_ALIAS',
  'CM_KEY_PASSWORD',
];
const missing = required.filter((name) => !String(process.env[name] || '').trim());
if (missing.length) {
  throw new Error(`Codemagic release signing is unavailable: ${missing.join(', ')}`);
}

const gradlePath = resolve('android/app/build.gradle');
let gradle = await readFile(gradlePath, 'utf8');

const expectedDebugSigning = `    signingConfigs {
        debug {
            storeFile file('debug.keystore')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
        }
    }`;

const codemagicSigning = `    signingConfigs {
        debug {
            storeFile file('debug.keystore')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
        }
        release {
            def cmKeystorePath = System.getenv("CM_KEYSTORE_PATH")
            if (cmKeystorePath == null || cmKeystorePath.trim().isEmpty()) {
                throw new GradleException("Codemagic release keystore is not available")
            }
            storeFile file(cmKeystorePath)
            storePassword System.getenv("CM_KEYSTORE_PASSWORD")
            keyAlias System.getenv("CM_KEY_ALIAS")
            keyPassword System.getenv("CM_KEY_PASSWORD")
        }
    }`;

const expectedReleaseSigning = `            signingConfig signingConfigs.debug`;
const buildTypesStart = gradle.indexOf('    buildTypes {');
const releaseStart = gradle.indexOf('        release {', buildTypesStart);
const releaseSigning = gradle.indexOf(expectedReleaseSigning, releaseStart);

if (!gradle.includes(expectedDebugSigning) || buildTypesStart < 0 || releaseStart < 0 || releaseSigning < releaseStart) {
  throw new Error('Unexpected Expo Android Gradle template; refusing to create a debug-signed release APK.');
}

gradle = gradle.replace(expectedDebugSigning, codemagicSigning);
// Adding the release signing block makes the original character offset stale;
// resolve the release block again so the debug build type is never changed.
const buildTypesStartAfterPatch = gradle.indexOf('    buildTypes {');
const releaseStartAfterPatch = gradle.indexOf('        release {', buildTypesStartAfterPatch);
const releaseSigningAfterPatch = gradle.indexOf(expectedReleaseSigning, releaseStartAfterPatch);
if (buildTypesStartAfterPatch < 0 || releaseStartAfterPatch < 0 || releaseSigningAfterPatch < releaseStartAfterPatch) {
  throw new Error('Expo release signing marker disappeared while applying the Codemagic patch.');
}
gradle = `${gradle.slice(0, releaseSigningAfterPatch)}            signingConfig signingConfigs.release${gradle.slice(releaseSigningAfterPatch + expectedReleaseSigning.length)}`;

if (!gradle.includes('signingConfig signingConfigs.release') || gradle.includes('release {\n            // Caution! In production, you need to generate your own keystore file.\n            // see https://reactnative.dev/docs/signed-apk-android.\n            signingConfig signingConfigs.debug')) {
  throw new Error('Codemagic release-signing patch verification failed.');
}

await writeFile(gradlePath, gradle, 'utf8');
console.log('CODEMAGIC_RELEASE_SIGNING_WIRED');

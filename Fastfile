# Fastlane configuration for automated Play Store deployment
# Place in: mobile/fastlane/Fastfile

default_platform(:android)

platform :android do
  desc "Build debug APK"
  lane :debug do
    gradle(task: "assembleDebug")

    # Copy to dist
    sh "mkdir -p ../../dist-mobile"
    sh "cp ../app/build/outputs/apk/debug/app-debug.apk ../../dist-mobile/MedOCR-debug.apk"
  end

  desc "Build and sign release APK"
  lane :release do
    gradle(
      task: "assembleRelease",
      properties: {
        "android.injected.signing.store.file" => ENV["KEYSTORE_PATH"],
        "android.injected.signing.store.password" => ENV["KEYSTORE_PASSWORD"],
        "android.injected.signing.key.alias" => ENV["KEY_ALIAS"],
        "android.injected.signing.key.password" => ENV["KEY_PASSWORD"]
      }
    )

    sh "mkdir -p ../../dist-mobile"
    sh "cp ../app/build/outputs/apk/release/app-release.apk ../../dist-mobile/MedOCR-release.apk"
  end

  desc "Build App Bundle for Play Store"
  lane :bundle do
    gradle(
      task: "bundleRelease",
      properties: {
        "android.injected.signing.store.file" => ENV["KEYSTORE_PATH"],
        "android.injected.signing.store.password" => ENV["KEYSTORE_PASSWORD"],
        "android.injected.signing.key.alias" => ENV["KEY_ALIAS"],
        "android.injected.signing.key.password" => ENV["KEY_PASSWORD"]
      }
    )

    sh "mkdir -p ../../dist-mobile"
    sh "cp ../app/build/outputs/bundle/release/app-release.aab ../../dist-mobile/MedOCR.aab"
  end

  desc "Deploy to Firebase App Distribution"
  lane :firebase do
    # Build first
    release

    # Distribute
    firebase_app_distribution(
      app: "1:123456789:android:abc123def456",
      testers: "tester1@example.com, tester2@example.com",
      groups: "testers, doctors",
      release_notes: "New build with offline sync improvements",
      firebase_cli_path: "/usr/local/bin/firebase"
    )
  end

  desc "Deploy to Google Play (Internal Testing)"
  lane :play_internal do
    # Build bundle
    bundle

    # Upload to Play Store
    upload_to_play_store(
      track: "internal",
      aab: "../app/build/outputs/bundle/release/app-release.aab",
      skip_upload_metadata: true,
      skip_upload_images: true,
      skip_upload_screenshots: true,
      release_status: "draft"
    )
  end

  desc "Deploy to Google Play (Beta)"
  lane :play_beta do
    bundle

    upload_to_play_store(
      track: "beta",
      aab: "../app/build/outputs/bundle/release/app-release.aab",
      release_notes: {
        'en-US' => "Beta release with new features"
      }
    )
  end

  desc "Deploy to Google Play (Production)"
  lane :play_production do
    bundle

    upload_to_play_store(
      track: "production",
      aab: "../app/build/outputs/bundle/release/app-release.aab",
      release_notes: {
        'en-US' => "Production release",
        'ar-SA' => "إصدار الإنتاج"
      }
    )
  end
end

const expo={
  name:'DOME',
  slug:'dome-mobile',
  owner:'bilingvadom',
  version:'3.1.2',
  scheme:'dome',
  icon:'./assets/branding/dome-app-icon-v2.png',
  orientation:'default',
  userInterfaceStyle:'automatic',
  ios:{
    supportsTablet:true,
    bundleIdentifier:'com.bilingvadom.dome',
    buildNumber:'3.1.1',
    infoPlist:{
      NSMicrophoneUsageDescription:'DOME uses the microphone during speaking activities when the parent has consented.',
      NSCameraUsageDescription:'DOME may use the camera for optional learning activities when the parent has enabled them.',
      NSPhotoLibraryUsageDescription:'DOME uses the photo library only when a parent chooses to upload a child hero.',
    },
  },
  android:{
    // Keep the installable Android app distinct from any historical package on
    // a tester device. This is an Android identity change only: it does not
    // change the DOME EAS project, lesson data, or backend.
    package:'com.bilingvadom.dome.mobile',
    versionCode:30102,
    softwareKeyboardLayoutMode:'resize',
    adaptiveIcon:{
      foregroundImage:'./assets/branding/dome-adaptive-foreground-v2.png',
      backgroundColor:'#063FC4',
    },
    permissions:['RECORD_AUDIO','INTERNET'],
    // Expo's Android template and transitive modules declare several optional
    // permissions that DOME does not use.  Block them in the merged release
    // manifest rather than relying on a source-only permissions list.
    blockedPermissions:[
      'android.permission.SYSTEM_ALERT_WINDOW',
      'android.permission.CAMERA',
      'android.permission.USE_BIOMETRIC',
      'android.permission.USE_FINGERPRINT',
    ],
  },
  plugins:[
    ['expo-splash-screen',{
      image:'./assets/branding/dome-splash-v2.png',
      resizeMode:'contain',
      backgroundColor:'#063FC4',
    }],
    'expo-asset',
    'expo-secure-store',
    'expo-status-bar',
    // Lesson audio is deliberately foreground-only (see LessonPlayer's
    // shouldPlayInBackground:false modes). Do not ship a media foreground
    // service or its extra permissions for a capability DOME does not offer.
    ['expo-audio',{enableBackgroundPlayback:false,enableBackgroundRecording:false}],
    'expo-video',
  ],
};

const buildCommit=String(process.env.EXPO_PUBLIC_BUILD_COMMIT||'unmarked').trim();
const buildTimestamp=String(process.env.EXPO_PUBLIC_BUILD_TIMESTAMP||'unknown-time').trim();

module.exports={
  ...expo,
  extra:{
    ...(expo.extra||{}),
    eas:{projectId:'54684562-e80c-49b7-a27b-6a36f1ad19b8'},
    domeBuildCommit:buildCommit,
    domeBuildTimestamp:buildTimestamp,
  },
};

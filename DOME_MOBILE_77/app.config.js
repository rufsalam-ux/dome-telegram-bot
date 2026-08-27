const {expo}=require('./app.json');

const buildCommit=String(process.env.EXPO_PUBLIC_BUILD_COMMIT||'unmarked').trim();
const buildTimestamp=String(process.env.EXPO_PUBLIC_BUILD_TIMESTAMP||'unknown-time').trim();

module.exports={
  ...expo,
  extra:{
    ...(expo.extra||{}),
    domeBuildCommit:buildCommit,
    domeBuildTimestamp:buildTimestamp,
  },
};

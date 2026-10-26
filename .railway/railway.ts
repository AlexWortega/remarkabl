import { defineRailway, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const cloudVolume = volume("cloud-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-east4-eqdc4a", sizeMB: 5000 });
  const diaryVolume = volume("diary-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-east4-eqdc4a", sizeMB: 5000 });
  const diary = service("diary", {
    healthcheck: "/health",
    healthcheckTimeout: 300,
    replicas: { "us-east4-eqdc4a": 1 },
    volumeMounts: { "/data": diaryVolume },
  });
  const cloud = service("cloud", {
    healthcheck: "/",
    healthcheckTimeout: 60,
    replicas: { "us-east4-eqdc4a": 1 },
    volumeMounts: { "/data": cloudVolume },
  });

  return project("remarkabl", {
    variables: { managed: false },
    resources: [diary, cloud, cloudVolume, diaryVolume],
  });
});

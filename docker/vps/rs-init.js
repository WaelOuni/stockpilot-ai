// Explicit first start only. The health check never changes membership.
const admin = db.getSiblingDB("admin");
if (!admin.auth("operator", fs.readFileSync("/run/operator-password", "utf8").trim())) quit(1);
const result = admin.runCommand({replSetInitiate: {_id: "rs0", members: [{_id: 0, host: "mongo:27017"}]}});
if (result.ok !== 1) quit(1);

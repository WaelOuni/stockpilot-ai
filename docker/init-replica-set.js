try {
    const status = rs.status();
    if (status.set !== "rs0") {
        throw new Error("Unexpected replica set name: " + status.set);
    }
} catch (error) {
    if (error.code !== 94) {
        throw error;
    }
    rs.initiate({
        _id: "rs0",
        members: [{ _id: 0, host: "mongodb:27017" }]
    });
}

if (!db.hello().isWritablePrimary) {
    quit(1);
}

// Read-only application-data fingerprint. Redirect output to a private file.
const { createHash } = require('crypto')
const names = db.adminCommand({ listDatabases: 1, nameOnly: true }).databases
  .map(item => item.name).filter(name => name.startsWith('stockpilot')).sort()
const result = {}
for (const name of names) {
  const database = db.getSiblingDB(name)
  result[name] = {}
  for (const info of database.getCollectionInfos().sort((a, b) => a.name.localeCompare(b.name))) {
    if (info.type === 'view') {
      result[name][info.name] = { type: 'view', options: info.options }
      continue
    }
    const collection = database.getCollection(info.name)
    const hash = createHash('sha256')
    let count = 0
    collection.find({}).sort({ _id: 1 }).forEach(document => {
      hash.update(EJSON.stringify(document, null, 0, { relaxed: false }) + '\n')
      count++
    })
    result[name][info.name] = {
      count, sha256: hash.digest('hex'), options: info.options,
      indexes: collection.getIndexes().sort((a, b) => a.name.localeCompare(b.name)),
    }
  }
}
print(EJSON.stringify(result, null, 0, { relaxed: false }))

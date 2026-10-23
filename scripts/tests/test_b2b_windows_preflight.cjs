const assert = require('node:assert/strict');
const p = require(require('node:path').resolve(process.argv[2]));
let count = 0;
function refuses(fn) { assert.throws(fn); count++; }
const nonce = 'a'.repeat(32);
const self = {pid:10,parent:0,name:'cscript.exe',command:'cscript helper.js '+nonce};
const benign = {pid:11,parent:0,name:'python.exe',command:'python harmless.py'};
assert.equal(p.processes([self,benign],nonce).positive_control,true); count++;
for (const command of ['docker compose up', 'docker container start x', 'docker --context x ps', 'docker.exe -H pipe image save x', 'docker-compose.exe up', 'python b2b_interactive.py start', 'node C:\\stockpilot\\admin.js']) {
    refuses(()=>p.processes([self,{...benign,command}],nonce));
}
refuses(()=>p.processes([benign],nonce));
refuses(()=>p.processes([self,self],nonce));
refuses(()=>p.processes([self,{...benign,command:null}],nonce));
refuses(()=>p.processes([self,{...benign,name:null}],nonce));
refuses(()=>p.processes([self,{...benign,pid:null}],nonce));
for (const value of [null,undefined,NaN,'bad']) { refuses(()=>p.pathComponent(value)); refuses(()=>p.capacity(value,30*1024**3)); }
refuses(()=>p.pathComponent(1024));
refuses(()=>p.capacity(1,30*1024**3));
assert.equal(p.capacity(40*1024**3,30*1024**3),40*1024**3); count++;
p.pathComponent(32); count++;
assert.equal(p.processes([self,benign],nonce,()=>false).exited_candidates_confirmed,1); count++;
refuses(()=>p.processes([self,{...benign,command:null}],nonce,()=>true));
refuses(()=>p.processes([self,benign],nonce,()=>{throw new Error('candidate_process_identity_changed');}));
console.log(JSON.stringify({result:'PASS',cases:count}));

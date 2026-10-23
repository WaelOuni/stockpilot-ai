/* Read-only Windows evidence for WSL cold preflight. No PowerShell or shell execution. */
function check(ok, code) { if (!ok) { throw new Error(code); } }
function number(value, code) {
    check(value !== null && value !== undefined && /^\d+$/.test(String(value)), code);
    var n = Number(value); check(isFinite(n) && n >= 0 && n <= 9007199254740991, code); return n;
}
function candidate(name) {
    return /^(docker(?:-compose)?|com\.docker\.cli|python[\w.-]*|py|uv|uvx|node|bash|sh|wsl|cmd|pwsh|powershell|ssh|scp|rsync|robocopy|tar|7z)(?:\.exe)?$/i.test(name);
}
function admin(command) {
    return /\b(?:docker(?:-compose)?(?:\.exe)?)(?:\s|"|$)/i.test(command) ||
        /stockpilot|b2b_interactive\.py|b2b_upgrade_manifest\.py|b2b_demo\.py/i.test(command);
}
function processes(rows, nonce, liveAgain) {
    var byId = {}, self = [], excluded = {}, checked = 0, exited = 0, i, r, pid;
    check(rows.length > 0, 'windows_process_inventory_empty');
    for (i = 0; i < rows.length; i++) {
        r = rows[i]; pid = number(r.pid, 'invalid_process_id');
        check(!byId[pid], 'duplicate_process_id'); byId[pid] = r;
        if (/^cscript\.exe$/i.test(r.name) && r.command !== null && r.command.indexOf(nonce) >= 0) { self.push(pid); }
    }
    check(self.length === 1, 'windows_positive_control_missing_or_ambiguous');
    pid = self[0];
    while (pid !== 0 && !excluded[pid]) {
        excluded[pid] = true;
        check(!!byId[pid], 'windows_ancestor_unobservable');
        pid = number(byId[pid].parent, 'invalid_parent_process_id');
        if (pid !== 0 && !byId[pid]) { break; } // A parent may have exited; never exclude its unknown replacement.
    }
    for (i = 0; i < rows.length; i++) {
        r = rows[i];
        check(typeof r.name === 'string' && r.name.length > 0, 'unknown_process_name');
        if (excluded[r.pid] || !candidate(r.name)) { continue; }
        if (liveAgain && !liveAgain(r)) { exited++; continue; }
        check(typeof r.command === 'string' && r.command.length > 0, 'candidate_commandline_unreadable');
        checked++;
        if (admin(r.command)) {
            var refusal = new Error('concurrent_windows_administration');
            refusal.process_id = Number(r.pid); refusal.process_name = r.name; throw refusal;
        }
    }
    return {positive_control: true, process_count: rows.length, candidates_checked: checked, exited_candidates_confirmed:exited, concurrent_admin: []};
}
function capacity(free, minimum) {
    free = number(free, 'windows_free_space_unknown');
    check(free >= minimum, 'physical_reserve_insufficient'); return free;
}
function pathComponent(attributes) { attributes = number(attributes, 'backing_attributes_unknown'); check((attributes & 1024) === 0, 'backing_path_reparse_point'); }
function quote(s) { return '"' + String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"').replace(/\r/g,'\\r').replace(/\n/g,'\\n') + '"'; }
function json(v) {
    var i, out = [];
    if (v === null) { return 'null'; }
    if (typeof v === 'string') { return quote(v); }
    if (typeof v === 'number' || typeof v === 'boolean') { return String(v); }
    if (v instanceof Array) { for (i = 0; i < v.length; i++) { out.push(json(v[i])); } return '[' + out.join(',') + ']'; }
    for (i in v) { if (Object.prototype.hasOwnProperty.call(v,i)) { out.push(quote(i) + ':' + json(v[i])); } }
    return '{' + out.join(',') + '}';
}
function run() {
    var stage = 'arguments';
    try {
        check(WScript.Arguments.length === 2, 'expected_nonce_and_minimum_bytes');
        var nonce = String(WScript.Arguments(0)), minimum = number(WScript.Arguments(1),'invalid_minimum');
        check(/^[a-f0-9]{32}$/.test(nonce) && minimum >= 30 * 1073741824, 'unsafe_arguments');
        stage = 'windows_processes';
        var wmi = GetObject('winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\cimv2');
        var list = new Enumerator(wmi.ExecQuery('SELECT ProcessId,ParentProcessId,Name,CommandLine,CreationDate FROM Win32_Process'));
        var rows = [], item;
        for (; !list.atEnd(); list.moveNext()) {
            item = list.item(); rows.push({pid:item.ProcessId,parent:item.ParentProcessId,name:item.Name,creation:item.CreationDate,command:item.CommandLine === null || item.CommandLine === undefined ? null : String(item.CommandLine)});
        }
        var evidence = processes(rows, nonce, function(row) {
            var again = new Enumerator(wmi.ExecQuery('SELECT Name,CreationDate FROM Win32_Process WHERE ProcessId=' + number(row.pid,'invalid_process_id')));
            if (again.atEnd()) { return false; }
            var current = again.item();
            check(row.creation !== null && row.creation !== undefined && current.CreationDate === row.creation && current.Name === row.name, 'candidate_process_identity_changed');
            again.moveNext(); check(again.atEnd(),'candidate_process_ambiguous'); return true;
        });
        stage = 'physical_capacity';
        list = new Enumerator(wmi.ExecQuery("SELECT DeviceID,FreeSpace,Size FROM Win32_LogicalDisk WHERE DeviceID='C:'"));
        check(!list.atEnd(),'physical_disk_missing'); item = list.item();
        var free = capacity(item.FreeSpace, minimum), size = number(item.Size,'disk_size_unknown');
        list.moveNext(); check(list.atEnd(),'physical_disk_ambiguous');
        stage = 'backing_files';
        var fso = new ActiveXObject('Scripting.FileSystemObject');
        var shell = new ActiveXObject('WScript.Shell');
        var reg = GetObject('winmgmts:{impersonationLevel=impersonate}!\\\\.\\root\\default:StdRegProv');
        function registry(method, key, name) {
            var input = reg.Methods_.Item(method).InParameters.SpawnInstance_();
            input.hDefKey = 2147483649; input.sSubKeyName = key;
            if (name !== null) { input.sValueName = name; }
            var output = reg.ExecMethod_(method,input);
            check(Number(output.ReturnValue) === 0, 'registry_query_failed'); return output;
        }
        var key = 'Software\\Microsoft\\Windows\\CurrentVersion\\Lxss';
        var keys = new VBArray(registry('EnumKey',key,null).sNames).toArray(), ubuntu = [];
        var i, sub, distro, values, names, types, j, base, baseType;
        for (i = 0; i < keys.length; i++) {
            sub = key + '\\' + keys[i]; distro = registry('GetStringValue',sub,'DistributionName').sValue;
            if (distro !== 'Ubuntu') { continue; }
            values = registry('EnumValues',sub,null);
            names = new VBArray(values.sNames).toArray(); types = new VBArray(values.Types).toArray(); baseType = null;
            for (j = 0; j < names.length; j++) { if (names[j] === 'BasePath') { baseType = types[j]; } }
            check(baseType === 1 || baseType === 2,'ubuntu_basepath_type_unknown');
            base = registry(baseType === 2 ? 'GetExpandedStringValue' : 'GetStringValue',sub,'BasePath').sValue;
            check(typeof base === 'string' && base.indexOf('%') < 0,'ubuntu_basepath_unknown');
            ubuntu.push(fso.BuildPath(base,'ext4.vhdx'));
        }
        check(ubuntu.length === 1,'ubuntu_registration_ambiguous');
        var docker = shell.ExpandEnvironmentStrings('%LOCALAPPDATA%') + '\\Docker\\wsl\\disk\\docker_data.vhdx';
        var paths = [ubuntu[0],docker], files = [], seen = {}, p, current, file;
        for (i = 0; i < paths.length; i++) {
            p = fso.GetAbsolutePathName(paths[i]);
            check(/^C:\\/i.test(p) && !seen[p.toLowerCase()], 'backing_file_drive_or_identity'); seen[p.toLowerCase()] = true;
            check(fso.FileExists(p),'backing_file_missing'); file = fso.GetFile(p);
            check(number(file.Size,'backing_file_size_unknown') > 0,'backing_file_empty'); pathComponent(file.Attributes);
            current = fso.GetParentFolderName(p);
            while (current !== '') {
                check(fso.FolderExists(current),'backing_parent_missing'); pathComponent(fso.GetFolder(current).Attributes);
                current = fso.GetParentFolderName(current);
            }
            files.push({path:p,length:Number(file.Size),drive:'C'});
        }
        WScript.Echo(json({version:1,result:'PASS',free_bytes:free,size_bytes:size,processes:evidence,backing_files:files}));
    } catch (e) {
        // Neither command lines nor COM exception descriptions may contain secrets in output.
        var reason = e.message || '';
        if (!/^[a-z_]{1,80}$/.test(reason)) { reason = 'windows_evidence_unavailable'; }
        var blocked = {version:1,result:'BLOCKED',stage:stage,reason:reason};
        if (reason === 'concurrent_windows_administration') { blocked.process_id = e.process_id; blocked.process_name = e.process_name; }
        WScript.Echo(json(blocked));
        WScript.Quit(1);
    }
}
if (typeof WScript !== 'undefined') { run(); }
if (typeof module !== 'undefined') { module.exports = {processes:processes,capacity:capacity,pathComponent:pathComponent,number:number,admin:admin}; }

const response = await fetch("/stats/dependencies/data");
const deps = await response.json();
const appKeys = Object.keys(deps.apps);

// Group app/branch keys by application name
// e.g. "charmcraft/hotfix/3.5" → appGroups["charmcraft"] = [{key, branch: "hotfix/3.5"}, ...]
const appGroups = {};
for (const key of appKeys) {
  const slash = key.indexOf("/");
  const appName = slash === -1 ? key : key.slice(0, slash);
  const branch = slash === -1 ? "main" : key.slice(slash + 1);
  if (!appGroups[appName]) appGroups[appName] = [];
  appGroups[appName].push({ key, branch });
}

// Build table
const div = document.getElementById("libs-and-apps-table");
if (!div) throw new Error("No #libs-and-apps-table div found");

const table = document.createElement("table");
const thead = document.createElement("thead");

// Header row 1: app name spanning branches
const headerRow1 = document.createElement("tr");
const libHeader = document.createElement("th");
libHeader.rowSpan = 2;
libHeader.textContent = "Library";
headerRow1.appendChild(libHeader);
for (const [appIndex, [appName, branches]] of Object.entries(appGroups).entries()) {
  const th = document.createElement("th");
  th.colSpan = branches.length;
  th.textContent = appName;
  th.className = appIndex === 0 ? "u-align--center" : "u-align--center app-group-header";
  headerRow1.appendChild(th);
}
thead.appendChild(headerRow1);

// Header row 2: branch names
const headerRow2 = document.createElement("tr");
for (const [appIndex, [, branches]] of Object.entries(appGroups).entries()) {
  for (const [i, { branch }] of branches.entries()) {
    const th = document.createElement("th");
    th.textContent = branch;
    th.className = i === 0 && appIndex > 0 ? "u-align--right app-group-start" : "u-align--right";
    headerRow2.appendChild(th);
  }
}
thead.appendChild(headerRow2);
table.appendChild(thead);

// Body: one row per library
const tbody = document.createElement("tbody");
for (const lib of deps.libs) {
  const tr = document.createElement("tr");
  const libCell = document.createElement("td");
  const libLink = document.createElement("a");
  libLink.href = "https://github.com/canonical/" + lib;
  libLink.textContent = lib;
  libLink.target = "_blank";
  libLink.rel = "noopener";
  libCell.appendChild(libLink);
  tr.appendChild(libCell);

  for (const [appIndex, [, branches]] of Object.entries(appGroups).entries()) {
    for (const [i, { key }] of branches.entries()) {
      const td = document.createElement("td");
      td.className = i === 0 && appIndex > 0 ? "u-align--right app-group-start" : "u-align--right";
      const depInfo = deps.apps[key]?.[lib];
      if (depInfo) {
        if (depInfo.version !== undefined) {
          if (depInfo.outdated) {
            td.classList.add("outdated");
            td.innerHTML = depInfo.version + "<br><small>(" + depInfo.latest + ")</small>";
          } else {
            td.textContent = depInfo.version;
          }
        } else {
          // Fallback: show version_spec for projects without uv.lock data
          td.textContent = depInfo.version_spec;
        }
      } else {
        td.textContent = "not used";
        td.classList.add("not-used");
      }
      tr.appendChild(td);
    }
  }
  tbody.appendChild(tr);
}
table.appendChild(tbody);
div.appendChild(table);

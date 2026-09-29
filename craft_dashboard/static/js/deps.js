// Dependency matrix: one row per craft library, one column per application
// branch. Data comes from /stats/dependencies/data.

const div = document.getElementById("libs-and-apps-table");
if (!div) throw new Error("No #libs-and-apps-table div found");

/** Replace the table area with an accessible, retryable error message. */
function renderError(message) {
  div.replaceChildren();
  const notification = document.createElement("div");
  notification.className = "p-notification--negative";
  notification.setAttribute("role", "alert");

  const content = document.createElement("div");
  content.className = "p-notification__content";

  const title = document.createElement("h5");
  title.className = "p-notification__title";
  title.textContent = "Unable to load dependencies";

  const text = document.createElement("p");
  text.className = "p-notification__message";
  text.textContent = message + " Reload the page to try again.";

  content.append(title, text);
  notification.appendChild(content);
  div.appendChild(notification);
}

function renderTable(deps) {
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
              td.appendChild(document.createTextNode(depInfo.version));
              td.appendChild(document.createElement("br"));
              const latest = document.createElement("small");
              latest.textContent = "(" + depInfo.latest + ")";
              td.appendChild(latest);
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
  div.replaceChildren(table);
}

try {
  const response = await fetch("/stats/dependencies/data");
  if (!response.ok) {
    renderError(`The server returned ${response.status} ${response.statusText}.`);
  } else {
    renderTable(await response.json());
  }
} catch (error) {
  console.error("Failed to load dependency data", error);
  renderError("The request failed, which usually means a network problem.");
}

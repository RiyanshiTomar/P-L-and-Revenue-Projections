# Putting the portal on the web (so the manager and others can open it)

**How it works.** The database is still built on your PC every morning (the build needs a lot of memory and the sheets). After the build, `refresh.py`
sends a **slim copy** of the database (71 MB, without raw rows, phone numbers or student names) to the web server. The web server only shows the pages,
behind a login. So the web portal always shows exactly what your PC shows, and nothing heavy runs on the server.

**Before you start (not technical):**
- Ask your manager / IT that company numbers may be hosted on an outside server. This is their decision, not ours.
- The server asks for a login. Anyone with the link but without a login sees nothing.
- Cost on Render: about $7 a month for the server + $0.5 a month for the 2 GB disk (check Render's price page).

## Steps (about 30 minutes)

### 1. Put the code on GitHub (the data stays on your PC)
1. Make a free account on github.com and create a **Private** repository, for example `gew-portal`. Never make it public.
2. On your PC, in the project folder (PowerShell):
```
cd "C:\Users\Riyanshi\P&L Projection and Dasboard"
git init
git add .
git commit -m "GEW portal"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/gew-portal.git
git push -u origin main
```
`.gitignore` already keeps `data/`, `*.xlsx`, `*.db` and the PDFs out of GitHub. Check on github.com that no `data` folder is there.

### 2. Make the server on Render
1. Make an account on render.com and connect your GitHub.
2. **New > Blueprint**, pick the repository. Render reads `render.yaml` (a Docker web service, a 2 GB disk at `/data`).
3. When it asks for the two secret settings, fill:
   - `GEW_USERS` = the logins, e.g. `riya:ChooseAPassword1,manager:ChooseAnotherPassword2` (name:password, separated by commas, no spaces).
   - `GEW_UPLOAD_TOKEN` = a long random text, for example 40 letters and digits. Keep it, you need it in step 3.
4. Deploy. When it is live you get a link like `https://gew-portal.onrender.com`. Until step 3 it has no data.

### 3. Connect your PC to the server (one time)
Make the file `data\deploy.json` on your PC with your link and the same token:
```
{"url": "https://gew-portal.onrender.com", "token": "THE-SAME-TOKEN-AS-IN-STEP-2"}
```
Then send the first database:
```
python deploy\push_to_server.py
```
Open the link, log in, and the portal is there. From now on `refresh.py` (the morning task) sends the new database by itself after every build.
The line `web portal updated: ...` in `data\refresh.log` shows that it worked.

### 4. Share
Send the link and a login to each person. To add or remove a person, change `GEW_USERS` in the Render dashboard (Environment) and save.

## Good to know
- The Executive Dashboard page reads the Google Sheets directly in the browser, so those sheets must stay shared as "anyone with the link: viewer".
- The Excel evidence file and the PDF are made on your PC; the "Download all proof (Excel)" button on the web also works, but without the batch start column.
- If the PC is off in the morning the web portal keeps showing the last data, and updates after the next refresh.
- To stop everything: delete the Render service. Nothing else holds the data.

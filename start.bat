@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  Launcher - New Product Pre-sales Process Data Admin System
rem
rem  Usage:
rem    start.bat            normal start (reuse services already running)
rem    start.bat restart    kill stale listeners on 8000 / 5173, then start
rem    start.bat stop       stop both services and exit
rem    start.bat status     show whether each service is alive, then exit
rem    start.bat check      start, then run the full availability check (API + all pages)
rem    start.bat verify     start, then run the whole acceptance chain (offline + online)
rem    start.bat clean      remove vite dep cache, then start
rem    start.bat backup     back up state files + MySQL dump, then exit
rem    start.bat delta      doc-delta for unclosed SKUs (snapshot + report), then exit
rem    start.bat help       print this list, then exit
rem
rem  NOTE: keep this file pure ASCII (no Chinese) to avoid cmd
rem        encoding issues with non-ASCII code pages.
rem ============================================================

set "ROOT=%~dp0"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

rem ---------- locate the node runtime ----------
rem The platform re-creates the versioned folder whenever it updates its
rem bundled node (22.22.2-2 -> 22.22.2-3 ...), so a hardcoded path silently
rem breaks this launcher. Resolve it at run time instead:
rem   1) trust versions\current when it points at a folder holding npm.cmd
rem   2) otherwise take the highest-sorting folder that holds npm.cmd
set "NODE_BASE=%USERPROFILE%\.workbuddy\binaries\node\versions"
set "NODE_DIR="
set "NODE_CUR="
if exist "%NODE_BASE%\current" set /p NODE_CUR=<"%NODE_BASE%\current"
if defined NODE_CUR if exist "%NODE_BASE%\!NODE_CUR!\npm.cmd" set "NODE_DIR=%NODE_BASE%\!NODE_CUR!"
if not defined NODE_DIR (
    for /f "delims=" %%D in ('dir /b /ad /o-n "%NODE_BASE%" 2^>NUL') do (
        if not defined NODE_DIR if exist "%NODE_BASE%\%%D\npm.cmd" set "NODE_DIR=%NODE_BASE%\%%D"
    )
)
if defined NODE_DIR set "NPM=%NODE_DIR%\npm.cmd"
set "BE_PORT=8000"
set "FE_PORT=5173"
set "BE_PROBE=http://127.0.0.1:8000/docs"
set "FE_PROBE=http://127.0.0.1:5173/"
set "FE_URL=http://127.0.0.1:5173"
set "VITE_META=%ROOT%frontend\node_modules\.vite\deps\_metadata.json"
set "FE_FORCE=0"

set "HAS_CURL=1"
where curl >NUL 2>&1
if errorlevel 1 set "HAS_CURL=0"

rem ---------- normalize the option word ----------
set "ARG=%~1"
set "OPT_KNOWN=0"
for %%O in (clean restart stop status check verify backup delta help) do (
    if /i "!ARG!"=="%%O" set "OPT_KNOWN=1"
)
if not "!ARG!"=="" if "!OPT_KNOWN!"=="0" (
    echo [WARN ] unknown option "!ARG!" - falling back to a normal start.
    echo         valid options: restart / stop / status / check / verify / clean / backup / delta / help
    echo.
)

if /i "!ARG!"=="help" (
    echo Options:
    echo   start.bat            normal start ^(reuse services already running^)
    echo   start.bat restart    kill stale listeners on 8000 / 5173, then start
    echo   start.bat stop       stop both services and exit
    echo   start.bat status     show whether each service is alive, then exit
    echo   start.bat check      start, then run the full availability check
    echo                        ^(API endpoints + all pages in a real browser^)
    echo   start.bat verify     same as check, plus the offline acceptance chain
    echo                        ^(backend tests / G506 baseline / category rules^)
    echo                        ^(plus the doc-delta model check^)
    echo   start.bat clean      remove vite dep cache, then start
    echo   start.bat backup     back up state files + MySQL dump, then exit
    echo   start.bat delta      doc-delta for unclosed SKUs: snapshot + report, then exit
    echo                        ^(extra args are forwarded, e.g. --days 7 --category X^)
    echo   start.bat help       print this list, then exit
    echo.
    pause
    exit /b 0
)

echo ============================================================
echo   Data Admin System - New Product Pre-sales Process
echo   Backend : FastAPI  http://127.0.0.1:8000
echo   Frontend: Vite dev http://127.0.0.1:5173
echo ============================================================
echo.

rem ---------- environment checks ----------
if not exist "%PY%" (
    echo [ERROR] Python venv not found:
    echo         %PY%
    echo         Create it first - see README.md, section 2.
    pause
    exit /b 1
)
if not defined NODE_DIR (
    echo [ERROR] No usable Node runtime found under:
    echo         %NODE_BASE%
    echo         Expected a single version folder that contains npm.cmd.
    echo         See README.md, section 2.
    pause
    exit /b 1
)
echo [INFO ] Python venv : %PY%
echo [INFO ] Node runtime: %NODE_DIR%

rem ---------- maintenance switches that run and exit ----------
if /i "!ARG!"=="status" (
    echo [CHECK] current service status
    echo.
    call :report_one "Backend " %BE_PORT% "%BE_PROBE%"
    call :report_one "Frontend" %FE_PORT% "%FE_PROBE%" vite
    echo.
    pause
    exit /b 0
)
if /i "!ARG!"=="stop" (
    echo [STOP ] stopping services on ports %BE_PORT% / %FE_PORT% ...
    call :kill_port %BE_PORT%
    call :kill_port %FE_PORT%
    ping -n 3 127.0.0.1 >NUL 2>&1
    echo.
    call :report_one "Backend " %BE_PORT% "%BE_PROBE%"
    call :report_one "Frontend" %FE_PORT% "%FE_PROBE%" vite
    echo.
    echo Stopped. Run start.bat to bring the system back up.
    echo.
    pause
    exit /b 0
)
if /i "!ARG!"=="backup" (
    echo [BACKUP] creating a backup under backups\ ...
    pushd "%ROOT%backend"
    "%PY%" etl\backup.py
    popd
    echo.
    pause
    exit /b 0
)
rem Doc-delta for unclosed SKUs (T10).  Runs offline - no need to start services:
rem it snapshots the current doc baseline and prints what changed since the last
rem snapshot.  Extra args (%2..%9) are forwarded to the python script.
if /i "!ARG!"=="delta" (
    echo [DELTA] doc-delta for unclosed SKUs: snapshot, then print the report ...
    echo.
    "%PY%" "%ROOT%scripts\verify_doc_delta.py" --record %2 %3 %4 %5 %6 %7 %8 %9
    echo.
    pause
    exit /b 0
)

rem ---------- switches that fall through into a normal start ----------
if /i "!ARG!"=="clean" (
    rem Do NOT wipe the whole cache dir here.  A bulk delete of the 100+ cache
    rem files can be refused by the environment's safe-delete guard (it counts
    rem deletions per turn), and a half-finished wipe leaves a broken cache.
    rem Invalidating the index is enough: vite re-optimizes the whole deps
    rem directory as soon as the index file is missing or stale.
    echo [CLEAN] invalidating vite dependency cache ...
    if exist "%VITE_META%" del /f /q "%VITE_META%" >NUL 2>&1
    rem The cache is invalidated, but a running dev server still holds the old
    rem module graph in memory - its port keeps listening while every page
    rem renders blank.  So clearing it must always bring the frontend down too.
    set "FE_FORCE=1"
)
if /i "!ARG!"=="restart" (
    echo [RESET] stopping stale listeners on ports %BE_PORT% / %FE_PORT% ...
    call :kill_port %BE_PORT%
    call :kill_port %FE_PORT%
    ping -n 2 127.0.0.1 >NUL 2>&1
)

rem ---------- frontend deps (first run only) ----------
if not exist "%ROOT%frontend\node_modules" (
    echo [INFO] First run: installing frontend dependencies, npm install ...
    pushd "%ROOT%frontend"
    call "%NPM%" install
    popd
    if errorlevel 1 (
        echo [ERROR] npm install failed. See the output above.
        pause
        exit /b 1
    )
)

rem ---------- backend ----------
rem Backend restarts are expensive (first request has to warm the in-process
rem caches), so reuse it whenever the port is answering. Port state alone is not
rem trusted here either - :probe_url confirms it actually replies over HTTP.
call :port_busy %BE_PORT%
if not errorlevel 1 (
    echo [START] Backend FastAPI on port %BE_PORT% ...
    start "backend-%BE_PORT%" /D "%ROOT%backend" cmd /k ""%PY%" run.py"
) else (
    call :probe_url "%BE_PROBE%"
    if errorlevel 1 (
        echo [RESET] Port %BE_PORT% is listening but not answering - restarting the backend.
        call :kill_port %BE_PORT%
        ping -n 2 127.0.0.1 >NUL 2>&1
        echo [START] Backend FastAPI on port %BE_PORT% ...
        start "backend-%BE_PORT%" /D "%ROOT%backend" cmd /k ""%PY%" run.py"
    ) else (
        echo [SKIP ] Port %BE_PORT% is already listening - reusing the running backend.
    )
)

rem ---------- frontend ----------
rem Watching the port is NOT enough to decide "the frontend is fine":
rem   - the vite dep cache may have been deleted while the server kept running
rem     (including by "start.bat clean"), leaving a process whose port answers
rem     but whose pages are blank;
rem   - the process may be alive yet already dying / restarting.  In that state
rem     "/" still answers 200 while every module path is answered with
rem     index.html, the browser reports
rem     "Uncaught SyntaxError: Unexpected token '<'" and every page is blank.
rem So the reuse test is "is /src/main.jsx really served as JS", not "is the
rem port listening".  Any other outcome must restart, never silently reuse.
if "!FE_FORCE!"=="1" (
    echo [RESET] vite dep cache was cleared - stopping the running frontend first
    call :kill_port %FE_PORT%
    ping -n 2 127.0.0.1 >NUL 2>&1
)
call :port_busy %FE_PORT%
if not errorlevel 1 (
    call :start_frontend
) else (
    if not exist "%VITE_META%" (
        echo [RESET] Port %FE_PORT% is listening but the vite dep cache is missing.
        echo         That state answers on the port yet renders a blank page,
        echo         so the frontend is being restarted instead of reused.
        call :restart_frontend
    ) else (
        call :probe_frontend
        if errorlevel 1 (
            echo [RESET] The frontend on port %FE_PORT% answers but is not serving a
            echo         JS module - its pages would be blank. Restarting it.
            call :restart_frontend
        ) else (
            echo [SKIP ] Port %FE_PORT% is already listening - reusing the running frontend.
        )
    )
)

rem ---------- wait until services really answer, then open browser ----------
echo.
echo [WAIT ] waiting for backend and frontend to become ready ...
call :wait_url "%BE_PROBE%" 60 "Backend "
call :wait_url "%FE_PROBE%" 180 "Frontend"

echo.
set "DO_ONLINE=0"
if /i "!ARG!"=="check" set "DO_ONLINE=1"
if /i "!ARG!"=="verify" set "DO_ONLINE=1"
rem "verify" additionally runs the offline acceptance chain (no browser needed):
rem backend tests, the G506 assessment baseline and the per-category rule sets.
if /i "!ARG!"=="verify" call :offline_checks

if "!DO_ONLINE!"=="1" (
    call :online_checks
) else (
    start "" "%FE_URL%"
    echo [DONE ] Opened in browser: %FE_URL%
)
echo.
echo If a page stays blank, run:  start.bat check
echo Full acceptance chain:       start.bat verify
echo To force a full restart:     start.bat restart
echo To check or stop:            start.bat status  ^|  start.bat stop
echo.
pause
exit /b 0

rem ============================================================
rem  :offline_checks    acceptance steps that need no browser
rem   These are the "did the change break the numbers" guards:
rem     1) backend test suite
rem     2) G506 baseline (the assessment clock starts at data-fetch,
rem        NOT at proposal - a regression here silently changes grades)
rem     3) per-category rule sets (mobile-phone cases have no
rem        data-fetch node: their clock starts at the proposal)
rem     4) doc-delta model (T10): the per-unclosed-SKU document
rem        increment.  Pure backend state, but its contracts decide
rem        whether the increment the business reads is real.
rem ============================================================
:offline_checks
set "PTS_RC=0"
set "T02_RC=0"
set "SHL_RC=0"
set "DD_RC=0"

echo [CHECK] backend test suite ...
echo.
pushd "%ROOT%"
rem --basetemp points at a standalone dir: pytest's teardown garbage
rem collection tries to remove the old Temp\pytest-of-dell tree, which trips
rem this machine's safe-delete guard (>50 entries) and SystemExit's the whole
rem process - with an explicit basetemp pytest skips that collection, so the
rem exit code can be trusted.
"%PY%" -m pytest tests -q --basetemp="%TEMP%\pytest-dt"
set "PTS_RC=!errorlevel!"
popd
echo.

echo [CHECK] G506 assessment baseline ...
"%PY%" "%ROOT%scripts\verify_t02.py"
set "T02_RC=!errorlevel!"
echo.

echo [CHECK] per-category rules (membrane / case) ...
"%PY%" "%ROOT%scripts\verify_shell_rules.py"
set "SHL_RC=!errorlevel!"
echo.

echo [CHECK] doc-delta model (unclosed SKUs) ...
"%PY%" "%ROOT%scripts\verify_doc_delta.py"
set "DD_RC=!errorlevel!"
echo.

if not "!PTS_RC!"=="0" echo [WARN ] backend tests failed - see the output above.
if not "!T02_RC!"=="0" echo [WARN ] G506 baseline changed - see the output above.
if not "!SHL_RC!"=="0" echo [WARN ] per-category rules check failed - see the output above.
if not "!DD_RC!"=="0" echo [WARN ] doc-delta model check failed - see the output above.
if "!PTS_RC!"=="0" if "!T02_RC!"=="0" if "!SHL_RC!"=="0" if "!DD_RC!"=="0" echo [DONE ] Offline acceptance chain passed.
echo.
exit /b 0

rem ============================================================
rem  :online_checks     the authoritative "can the pages open" verdict
rem   The port listening is NOT the answer: browser_check.py really launches
rem   Chromium and renders every page.  A listening port with a blank page is
rem   exactly the failure mode seen before (dev server "half dead").
rem ============================================================
:online_checks
echo [CHECK] 1/4 - scanning all backend API endpoints ...
echo.
"%PY%" "%ROOT%scripts\scan_endpoints.py"
set "API_RC=!errorlevel!"
echo.

echo [CHECK] 2/4 - rendering every page in a real headless browser ...
echo.
pushd "%ROOT%frontend"
"%PY%" scripts\browser_check.py
set "CHECK_RC=!errorlevel!"
popd
echo.

echo [CHECK] 3/4 - live category smoke (membrane vs case) ...
echo.
"%PY%" "%ROOT%scripts\verify_category_live.py" "http://127.0.0.1:%BE_PORT%"
set "CAT_RC=!errorlevel!"
echo.

rem 4) The only check that can see INSIDE a dialog: browser_check only dumps the
rem    DOM, and MUI dialogs are not mounted while closed.  This one drives a real
rem    Chromium over CDP, opens the SKU dialog and actually clicks the download
rem    buttons, then verifies the files really landed on disk.
echo [CHECK] 4/4 - clicking the SKU-dialog download buttons (CDP) ...
echo.
pushd "%ROOT%frontend"
"%PY%" scripts\verify_review_downloads.py
set "DL_RC=!errorlevel!"
popd
echo.

if "!CHECK_RC!"=="0" (
    echo [DONE ] All pages rendered successfully.
) else (
    echo [WARN ] Some pages failed - see the list above.
)
if not "!API_RC!"=="0" echo [WARN ] Some backend endpoints did not return 200 - see the list above.
if not "!CAT_RC!"=="0" echo [WARN ] Category smoke failed - see the list above.
if not "!DL_RC!"=="0" echo [WARN ] SKU-dialog download buttons failed - see the list above.
if "!CHECK_RC!"=="0" if "!API_RC!"=="0" if "!CAT_RC!"=="0" if "!DL_RC!"=="0" echo [DONE ] System is usable: backend + frontend + categories + dialog downloads all verified.
echo.
echo         Opening the in-browser self-check as well ...
start "" "%FE_URL%/selfcheck.html"
exit /b 0

rem ============================================================
rem  :port_busy <port>   errorlevel 0 = free, 1 = something listening
rem ============================================================
:port_busy
netstat -ano | findstr /c:"LISTENING" | findstr /c:":%~1 " >NUL 2>&1
if errorlevel 1 exit /b 0
exit /b 1

rem ============================================================
rem  :kill_port <port>   stop whatever listens on that port
rem  NOTE: killing the wrapper shell is not enough - the child that
rem        owns the LISTENING socket keeps the port and the DB
rem        handle. Always kill the PID from netstat.
rem ============================================================
:kill_port
set "KP_FOUND=0"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:"LISTENING" ^| findstr /c:":%~1 "') do (
    set "KP_FOUND=1"
    echo   [KILL] port %~1 held by PID %%P
    taskkill /F /PID %%P >NUL 2>&1
)
if "!KP_FOUND!"=="0" echo   [SKIP] port %~1 - nothing listening
exit /b 0

rem ============================================================
rem  :start_frontend     launch the vite dev server in its own window
rem ============================================================
:start_frontend
echo [START] Frontend Vite dev on port %FE_PORT% ...
start "frontend-%FE_PORT%" /D "%ROOT%frontend" cmd /k ""%NPM%" run dev"
exit /b 0

rem ============================================================
rem  :restart_frontend   kill whatever holds the port, then start fresh
rem ============================================================
:restart_frontend
call :kill_port %FE_PORT%
ping -n 2 127.0.0.1 >NUL 2>&1
call :start_frontend
exit /b 0

rem ============================================================
rem  :probe_frontend     errorlevel 0 = serving a real JS module
rem  A dev server that is dying still answers on "/" with 200 while handing
rem  index.html to every module request - pages then render blank.  Only
rem  scripts\probe_frontend.py can tell those two states apart.
rem ============================================================
:probe_frontend
"%PY%" "%ROOT%scripts\probe_frontend.py" "%FE_URL%"
exit /b %errorlevel%

rem ============================================================
rem  :probe_url <url>    errorlevel 0 = answering (HTTP 200)
rem ============================================================
:probe_url
set "PR_OK=0"
if "!HAS_CURL!"=="1" (
    curl -s -f -o NUL --max-time 3 --noproxy "*" "%~1" >NUL 2>&1
    if not errorlevel 1 set "PR_OK=1"
) else (
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -Command "[System.Net.WebRequest]::DefaultWebProxy=$null; try{if((Invoke-WebRequest -Uri '%~1' -UseBasicParsing -TimeoutSec 3).StatusCode -eq 200){'OK'}}catch{}"`) do set "PR_OK=1"
)
if "!PR_OK!"=="1" exit /b 0
exit /b 1

rem ============================================================
rem  :wait_url <url> <max_seconds> <label>
rem  NOTE: waits use "ping -n N+1 127.0.0.1" rather than "timeout /t N".
rem        timeout.exe refuses to run when stdin is redirected / there is no
rem        console ("input redirection is not supported") and aborts the whole
rem        subroutine, which used to break this loop and skip the wait.
rem        ping always just waits, whatever the console looks like.
rem ============================================================
:wait_url
set "WU_URL=%~1"
set "WU_MAX=%~2"
set "WU_NAME=%~3"
set /a WU_TRIES=0
:wait_url_loop
call :probe_url "%WU_URL%"
if not errorlevel 1 (
    echo [OK   ] %WU_NAME% ready   ^(%WU_URL%^)
    exit /b 0
)
set /a WU_TRIES+=1
if !WU_TRIES! GEQ %WU_MAX% (
    echo [WARN ] %WU_NAME% no response after %WU_MAX%s  ^(%WU_URL%^)
    exit /b 1
)
ping -n 2 127.0.0.1 >NUL 2>&1
goto wait_url_loop

rem ============================================================
rem  :report_one <label> <port> <probe_url> [vite]
rem  one line per service: port state + HTTP state
rem  pass "vite" as the 4th arg to also show the dev-server dep cache state
rem ============================================================
:report_one
set "RO_NAME=%~1"
set "RO_PORT=%~2"
set "RO_URL=%~3"
set "RO_META_TXT="
call :port_busy %RO_PORT%
if errorlevel 1 (set "RO_PORT_TXT=LISTENING") else (set "RO_PORT_TXT=free     ")
call :probe_url "%RO_URL%"
if not errorlevel 1 (set "RO_HTTP_TXT=HTTP 200 OK") else (set "RO_HTTP_TXT=no response")
rem NOTE: inside set "..." the pipe needs no ^ - the value is not re-parsed,
rem       so a ^ there would be printed literally (it used to show up as "^|").
if /i "%~4"=="vite" (
    if exist "%VITE_META%" (
        set "RO_META_TXT=  |  dep cache ok"
    ) else (
        set "RO_META_TXT=  |  dep cache MISSING (pages would render blank)"
    )
    call :probe_frontend >NUL 2>&1
    if errorlevel 1 (
        set "RO_META_TXT=!RO_META_TXT!  |  modules NOT served (every page blank)"
    ) else (
        set "RO_META_TXT=!RO_META_TXT!  |  modules ok"
    )
)
echo [STATUS] %RO_NAME% port %RO_PORT% : !RO_PORT_TXT!  ^|  !RO_HTTP_TXT!!RO_META_TXT!
exit /b 0

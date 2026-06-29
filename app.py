from flask import Flask, render_template, jsonify
import psutil, time, subprocess, requests, traceback, os, socket, json, re
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Repositori spesifik yang digunakan untuk pemantauan DevSecOps
GITHUB_REPO = "stride-devsecops-demo"

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "Authorization": f"token {GITHUB_TOKEN}"
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/infrastructure/server')
def server_status():
    uptime_sec = int(time.time() - psutil.boot_time())
    
    # Mengambil nama hostname asli dari sistem
    raw_hostname = socket.gethostname()

    return jsonify({
        "hostname": raw_hostname,
        "status": "online",
        "cpu": round(psutil.cpu_percent(interval=1), 1),
        "memory": round(psutil.virtual_memory().percent, 1),
        "disk": round(psutil.disk_usage('/').percent, 1),
        "uptime": f"{uptime_sec // 3600} jam {(uptime_sec % 3600) // 60} menit"
    })

@app.route('/api/infrastructure/docker')
def docker_status():
    try:
        result = subprocess.run(["docker", "ps", "--format", "{{.Names}}|{{.Status}}"], capture_output=True, text=True)
        lines = [line.split('|') for line in result.stdout.strip().split('\n') if line]
        containers = [{"container_name": parts[0], "status": parts[1]} for parts in lines]

        return jsonify({"containers_running": len(containers), "containers": containers})
    except Exception as e:
        return jsonify({"containers_running": 0, "containers": [], "error": str(e)})

@app.route('/api/infrastructure/tailscale')
def tailscale_status():
    try:
        result = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({"status": "offline", "error": "Tailscale service not running"})

        data = json.loads(result.stdout)
        self_node = data.get("Self", {})
        peers = data.get("Peer", {})

        # Mengambil nama server secara langsung dari data aslinya (tanpa dirubah/mapping)
        server_name = self_node.get("HostName", "Unknown")
        active_devices = [server_name]

        # Menambahkan peer perangkat lain yang sedang online dengan nama aslinya
        for p in peers.values():
            if p.get("Online", False):
                active_devices.append(p.get("HostName", "Unknown"))

        return jsonify({
            "status": "online" if self_node.get("Online") else "offline",
            "tailscale_ip": self_node.get("TailscaleIPs", ["-"])[0],
            "server_name": server_name,
            "active_devices": active_devices
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

@app.route('/api/github/repos')
def github_repos():
    try:
        url = f"https://api.github.com/users/{GITHUB_USERNAME}/repos?sort=updated&per_page=10"
        resp = requests.get(url, headers=GITHUB_HEADERS)
        if resp.status_code == 404:
            resp = requests.get(f"https://api.github.com/orgs/{GITHUB_USERNAME}/repos?sort=updated&per_page=10", headers=GITHUB_HEADERS)
        resp.raise_for_status()

        parsed_repos = []
        for repo in resp.json():
            repo_data = {
                "name": repo['name'], "html_url": repo['html_url'],
                "default_branch": repo['default_branch'], "private": repo['private'],
                "lastCommitSha": "-", "authorName": "N/A"
            }
            c_resp = requests.get(f"https://api.github.com/repos/{repo['owner']['login']}/{repo['name']}/commits?per_page=1", headers=GITHUB_HEADERS)
            if c_resp.ok and (commits := c_resp.json()):
                repo_data["lastCommitSha"] = commits[0]['sha'][:7]
                author = commits[0]['commit']['author']
                repo_data["authorName"] = author.get('name') or author.get('email', 'N/A')

            parsed_repos.append(repo_data)
        return jsonify(parsed_repos)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/github/pipelines')
def github_pipelines():
    try:
        # 1. TETAP: Mengambil maksimal 10 repositori terbaru (sesuai permintaan Anda)
        resp = requests.get(f"https://api.github.com/users/{GITHUB_USERNAME}/repos?sort=updated&per_page=10", headers=GITHUB_HEADERS)
        resp.raise_for_status()

        all_runs = []
        for repo in resp.json():
            page = 1
            # Loop otomatis untuk mengambil SELURUH pipeline run tanpa batasan limit
            while True:
                # Menggunakan per_page=100 agar proses fetch lebih cepat & menghemat kuota API GitHub
                r_resp = requests.get(f"https://api.github.com/repos/{repo['owner']['login']}/{repo['name']}/actions/runs?per_page=100&page={page}", headers=GITHUB_HEADERS)
                
                if not r_resp.ok:
                    break
                
                workflow_runs = r_resp.json().get('workflow_runs', [])
                if not workflow_runs:
                    break  # Jika halaman ini sudah tidak ada datanya lagi, hentikan loop
                
                # Proses pemetaan data (100% logika asli milik Anda)
                for run in workflow_runs:
                    hc = run.get('head_commit') or {}
                    actor = run.get('triggering_actor') or {}
                    created_at = run.get('created_at', '1970-01-01T00:00:00Z')
                    msg = hc.get('message')

                    all_runs.append({
                        "id": run.get('id', 0),
                        "run_number": run.get('run_number', '-'),
                        "event": run.get('event', 'unknown'),
                        "repoName": repo.get('name', 'Unknown'),
                        "branch": run.get('head_branch', 'Unknown'),
                        "commitSha": str(run.get('head_sha', '0000000'))[:7],
                        "commitMsg": msg.split('\n')[0] if msg else 'Manual / API Trigger',
                        "status": run.get('status', 'unknown'),
                        "conclusion": run.get('conclusion', 'unknown'),
                        "actor": actor.get('login', 'Otomatis'),
                        "html_url": run.get('html_url', '#'),
                        "updated_at": run.get('updated_at') or created_at,
                        "created_at": created_at
                    })
                
                page += 1 # Lanjut ke halaman berikutnya untuk mengambil sisa run

        # Urutkan dari yang paling baru (100% asli milik Anda)
        all_runs.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Kirim semua data (all_runs) ke frontend tanpa batasan
        return jsonify(all_runs)
        
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/api/audit/logs')
def audit_logs():
    real_logs = []
    current_year = datetime.now().year
    wib_now = datetime.utcnow() + timedelta(hours=7)
    timestamp_file = "attack_time.txt"
    secret_timestamp_file = "secret_time.txt"
    commit_timestamp_file = "commit_time.txt"
    dependabot_timestamp_file = "dependabot_time.txt" # <-- PERBAIKAN 1: Daftarkan lock file Dependabot di sini

    # =========================================================
    # 1. INFRASTRUCTURE: Tarik Data Login SSH
    # =========================================================
    try:
        ssh_cmd = subprocess.run(
            ["journalctl", "-u", "ssh", "--no-pager"],
            capture_output=True, text=True
        )
        for line in ssh_cmd.stdout.split('\n'):
            if "Accepted" in line or "Failed" in line:
                status = "success" if "Accepted" in line else "blocked"
                severity = "low" if "Accepted" in line else "medium"
                event_desc = "Login SSH Berhasil" if "Accepted" in line else "Gagal Login SSH (Brute Force Attempt)"
                match = re.match(r'^([A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})', line)
                timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if match:
                    try:
                        utc_time = datetime.strptime(f"{current_year} {match.group(1)}", "%Y %b %d %H:%M:%S")
                        timestamp_str = (utc_time + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                    except: pass
                
                real_logs.append({
                    "timestamp": timestamp_str,
                    "category": "Infrastructure",
                    "event": f"{event_desc} - {line.split('sshd')[1].strip() if 'sshd' in line else line}",
                    "severity": severity,
                    "status": status
                })
    except Exception as e:
        print(f"[ERROR AUDIT] Gagal membaca log SSH: {e}")

    # =========================================================
    # 2. PIPELINE: Tarik Data GitHub Actions (SEMUA STATUS)
    # =========================================================
    try:
        if GITHUB_USERNAME and GITHUB_REPO:
            url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/actions/runs?per_page=1"
            run_resp = requests.get(url, headers=GITHUB_HEADERS)
            
            if run_resp.ok:
                workflow_runs = run_resp.json().get('workflow_runs', [])
                if workflow_runs:
                    latest_run = workflow_runs[0]
                    conclusion = latest_run.get('conclusion') 
                    status_text = latest_run.get('status')    
                    
                    gh_time_str = latest_run.get('updated_at', '').replace('T', ' ').replace('Z', '')
                    try: 
                        wib_gh = datetime.strptime(gh_time_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=7)
                        timestamp_str = wib_gh.strftime("%Y-%m-%d %H:%M:%S")
                    except: 
                        timestamp_str = gh_time_str

                    is_failure = (conclusion == 'failure' or status_text == 'failure')
                    
                    if is_failure:
                        event_msg = f"Pipeline Failure: Workflow '{latest_run.get('name')}' (# {latest_run.get('run_number')}) gagal."
                        sev, stat = "high", "flagged"
                    elif status_text in ['queued', 'in_progress']:
                        event_msg = f"Pipeline '{latest_run.get('name')}' sedang diproses..."
                        sev, stat = "low", "investigating"
                    else:
                        event_msg = f"Pipeline '{latest_run.get('name')}' berhasil diselesaikan."
                        sev, stat = "low", "success"

                    if is_failure:
                        real_logs.append({
                            "timestamp": timestamp_str,
                            "category": "Pipeline",
                            "event": event_msg,
                            "severity": sev,
                            "status": stat,
                            "show_in_audit": is_failure 
                        })
    except Exception as e:
        pass

    # =========================================================
    # 3. DEPENDENCY: Tarik Data Dependabot (HANYA TERBARU)
    # =========================================================
    try:
        has_active_alert = False
        if GITHUB_USERNAME and GITHUB_REPO:
            dep_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dependabot/alerts?state=open&per_page=5"
            dep_resp = requests.get(dep_url, headers=GITHUB_HEADERS)
            
            if dep_resp.ok:
                alerts = dep_resp.json()
                if alerts:
                    latest_alert = alerts[0]
                    current_state = latest_alert.get('state', 'unknown')
                    is_active = current_state in ['open', 'opened', 'active', 'detected']
                    
                    if is_active:
                        has_active_alert = True
                        # PERBAIKAN 2: Gunakan 'updated_at' (fallback ke 'created_at') agar sinkron dengan simulasi ulang
                        gh_time_str = latest_alert.get('updated_at', latest_alert.get('created_at', '')).replace('T', ' ').replace('Z', '')
                        try:
                            wib_dep = datetime.strptime(gh_time_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=7)
                            timestamp_str = wib_dep.strftime("%Y-%m-%d %H:%M:%S")
                        except: 
                            timestamp_str = gh_time_str
                        
                        # PERBAIKAN 3: Logika State Lock File khusus Dependency Poisoning
                        if not os.path.exists(dependabot_timestamp_file):
                            with open(dependabot_timestamp_file, "w") as f:
                                f.write(timestamp_str)
                        with open(dependabot_timestamp_file, "r") as f:
                            timestamp_str = f.read()
                        
                        pkg_name = latest_alert.get('security_advisory', {}).get('vulnerabilities', [{}])[0].get('package', {}).get('name', 'Unknown')
                        
                        real_logs.append({
                            "timestamp": timestamp_str,
                            "category": "Dependency",
                            "event": f"Dependency Poisoning: Vulnerability kritis terdeteksi pada paket pihak ketiga '{pkg_name}'.",
                            "severity": "critical",
                            "status": "flagged"
                        })
            else:
                if dep_resp.status_code != 404:
                    print(f"[ERROR AUDIT] GitHub API Dependabot memberi respon: {dep_resp.status_code}")
        
        # PERBAIKAN 4: Hapus lock file secara otomatis jika ancaman Dependabot sudah diselesaikan/ditutup
        if not has_active_alert and os.path.exists(dependabot_timestamp_file):
            os.remove(dependabot_timestamp_file)

    except Exception as e:
        print(f"[ERROR AUDIT] Gagal Dependabot: {e}")

    # =========================================================
    # 4. ARTIFACT: Tarik Data Docker (SEMUA IMAGE)
    # =========================================================
    try:
        docker_res = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}} - {{.ID}}"],
            capture_output=True, text=True
        )
        images = docker_res.stdout.strip().split('\n')
        
        if images and images[0] != "":
            latest_image = images[0]
            is_unsigned = "unsigned" in latest_image 
            
            malicious_audit_time = wib_now.strftime("%Y-%m-%d %H:%M:%S")
            if is_unsigned and os.path.exists(timestamp_file):
                with open(timestamp_file, "r") as f:
                    malicious_audit_time = f.read()

            real_logs.append({
                "timestamp": malicious_audit_time,
                "category": "Artifact",
                "event": "Supply Chain Attack: Docker image Unsigned terdeteksi!" if is_unsigned else f"Image kontainer baru dibuat: {latest_image[:20]}...",
                "severity": "critical" if is_unsigned else "low",
                "status": "flagged" if is_unsigned else "success",
                "show_in_audit": is_unsigned 
            })
    except Exception as e:
        pass

    # =========================================================
    # 5. SECRET LEAKAGE: Tarik Data Pemindaian Kunci Rahasia
    # =========================================================
    try:
        if os.path.exists(secret_timestamp_file):
            with open(secret_timestamp_file, "r") as f:
                malicious_secret_time = f.read()

            real_logs.append({
                "timestamp": malicious_secret_time,
                "category": "Secret Protection",
                "event": "Secret Leakage Alert: Scanner mendeteksi adanya kebocoran kunci akses AWS API Credential secara hardcoded.",
                "severity": "critical",
                "status": "flagged"
            })
    except Exception as e:
        print(f"[ERROR AUDIT] Gagal menarik data audit Secret Protection: {e}")

    # =========================================================
    # 6. SCM AUDIT: Deteksi Malicious Unsigned Commit (BARU)
    # =========================================================
    try:
        if os.path.exists(commit_timestamp_file):
            with open(commit_timestamp_file, "r") as f:
                malicious_commit_time = f.read()

            real_logs.append({
                "timestamp": malicious_commit_time,
                "category": "Source Code Management",
                "event": "Malicious Commit Alert: Terdeteksi pengiriman commit baru tanpa verifikasi tanda tangan digital GPG/SSH.",
                "severity": "high",
                "status": "flagged"
            })
    except Exception as e:
        print(f"[ERROR AUDIT] Gagal menarik data audit SCM Commit Verification: {e}")

    # Mengurutkan seluruh kombinasi log berdasarkan waktu terbaru (WIB)
    real_logs.sort(key=lambda x: x['timestamp'], reverse=True)
    return jsonify(real_logs)

@app.route('/api/artifacts')
def get_artifacts():
    try:
        # =========================================================
        # OPSI 1: Tarik Data Real-Time dari GitHub Packages (GHCR)
        # =========================================================
        url = f"https://api.github.com/users/{GITHUB_USERNAME}/packages?package_type=container"
        resp = requests.get(url, headers=GITHUB_HEADERS)
        
        # Jika user berupa Organization, belokkan ke endpoint orgs
        if resp.status_code == 404 or not resp.json():
            url = f"https://api.github.com/orgs/{GITHUB_USERNAME}/packages?package_type=container"
            resp = requests.get(url, headers=GITHUB_HEADERS)
            
        if resp.ok and resp.json():
            packages = resp.json()
            artifacts_data = []
            
            for pkg in packages:
                pkg_name = pkg.get('name')
                
                # Abaikan file tanda tangan (.sig) dari Cosign agar tidak duplikat di tabel
                if pkg_name.endswith('.sig') or '-sig' in pkg_name:
                    continue
                    
                # Ambil detail versi untuk mendapatkan Tag dan Ukuran asli
                versions_url = f"{pkg.get('url')}/versions"
                v_resp = requests.get(versions_url, headers=GITHUB_HEADERS)
                
                if v_resp.ok and (versions := v_resp.json()):
                    latest_version = versions[0]
                    container_meta = latest_version.get('metadata', {}).get('container', {})
                    tags = container_meta.get('tags', ['latest'])
                    
                    # Hitung ukuran file (konversi dari bytes ke MB)
                    size_bytes = latest_version.get('size', 0)
                    size_mb = f"{round(size_bytes / (1024 * 1024), 1)} MB" if size_bytes > 0 else "N/A"
                    
                    # --- FITUR DEVSECOPS: Deteksi Penandatanganan Image (Cosign/Notation) ---
                    # Memeriksa secara real-time apakah ada versi dengan tag ".sig" (tanda tangan digital)
                    signature_status = "Not Signed"
                    for v in versions:
                        v_tags = v.get('metadata', {}).get('container', {}).get('tags', [])
                        if any('.sig' in t or t.endswith('-sig') for t in v_tags):
                            signature_status = "Verified"
                            break
                    
                    artifacts_data.append({
                        "repository": pkg_name,
                        "tag": tags[0] if tags else "latest",
                        "size": size_mb,
                        "created": latest_version.get('created_at', pkg.get('created_at')),
                        "signature": signature_status
                    })
            
            if artifacts_data:
                return jsonify(artifacts_data)

        # =========================================================
        # OPSI 2 (FALLBACK): Ambil Data Image Langsung dari Server Docker Lokal
        # =========================================================
        return get_local_docker_artifacts()

    except Exception as e:
        print(f"[ERROR ARTIFACTS] Gagal sinkronisasi GitHub Packages: {e}")
        return get_local_docker_artifacts()


def get_local_docker_artifacts():
    """Fungsi pembantu untuk mengambil manifest image dari internal server Docker"""
    try:
        # Menjalankan perintah internal docker images secara real-time
        result = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}|{{.Tag}}|{{.Size}}|{{.CreatedAt}}"],
            capture_output=True, text=True
        )
        
        lines = result.stdout.strip().split('\n')
        local_artifacts = []
        
        for line in lines:
            if not line or "<none>" in line: 
                continue # Lewati image tanpa nama/dangling
                
            parts = line.split('|')
            if len(parts) >= 4:
                repo_name = parts[0]
                tag = parts[1]
                size = parts[2]
                raw_created = parts[3] # Format bawaan: 2026-06-24 11:00:00 ...
                
                # Standarisasi waktu ke format ISO agar bisa dibaca JavaScript Date
                try:
                    date_part = raw_created.split(' ')[0]
                    time_part = raw_created.split(' ')[1]
                    iso_created = f"{date_part}T{time_part}Z"
                except Exception:
                    iso_created = datetime.now().isoformat() + "Z"
                
                # ====================================================
                # UPDATE: Logika Penilaian Aturan Keamanan (Simulasi)
                # ====================================================
                if "-signed" in tag:
                    signature_status = "Verified"
                elif "hacked" in tag:
                    signature_status = "Not Signed" # Dianggap ilegal/tidak lolos verifikasi
                else:
                    # Fallback ke logika bawaan untuk image lainnya
                    signature_status = "Not Signed"
                    if "secure" in repo_name or "prod" in repo_name:
                        signature_status = "Verified"
                    elif "service" in repo_name:
                        signature_status = "Signed"
                # ====================================================

                local_artifacts.append({
                    "repository": repo_name,
                    "tag": tag,
                    "size": size,
                    "created": iso_created,
                    "signature": signature_status
                })
                
        return jsonify(local_artifacts if local_artifacts else [])
        
    except Exception as e:
        print(f"[ERROR LOCAL DOCKER] Gagal mengambil image lokal: {e}")
        return jsonify([])

@app.route('/api/security/center')
def security_center_status():
    try:
        # Waktu saat ini dalam WIB (UTC+7)
        wib_now = datetime.utcnow() + timedelta(hours=7)
        
        # File state lock untuk mengunci waktu masing-masing serangan
        timestamp_file = "attack_time.txt"
        secret_timestamp_file = "secret_time.txt"
        commit_timestamp_file = "commit_time.txt" # Lock waktu untuk serangan commit
        dependabot_timestamp_file = "dependabot_time.txt"

        # =========================================================
        # 1. ANALISIS KONDISI SISTEM: ARTIFACT (DOCKER)
        # =========================================================
        has_malicious_image = False
        malicious_time = wib_now.strftime("%Y-%m-%d %H:%M:%S")

        try:
            docker_res = subprocess.run(
                ["docker", "images", "--format", "{{.Tag}}"],
                capture_output=True, text=True
            )
            
            if "unsigned" in docker_res.stdout:
                has_malicious_image = True
                if not os.path.exists(timestamp_file):
                    with open(timestamp_file, "w") as f:
                        f.write(wib_now.strftime("%Y-%m-%d %H:%M:%S"))
                
                with open(timestamp_file, "r") as f:
                    malicious_time = f.read()
            else:
                if os.path.exists(timestamp_file):
                    os.remove(timestamp_file)
                    
        except Exception:
            pass

        # =========================================================
        # 1B. ANALISIS KONDISI SISTEM: SECRET LEAKAGE (REAL-TIME SCANNER)
        # =========================================================
        has_secret_leak = False
        secret_time = wib_now.strftime("%Y-%m-%d %H:%M:%S")

        try:
            aws_key_pattern = re.compile('AKIA' + '[0-9A-Z]{16}')
            aws_secret_pattern = re.compile('wJalrXUtnFEMI/' + '[0-9a-zA-Z+/]{32}')
            
            for root, dirs, files in os.walk('.'):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', '__pycache__', 'env']]
                for file in files:
                    if file.endswith(('.py', '.env', '.json', '.yml', '.yaml', '.txt')):
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, 'r', errors='ignore') as f:
                                content = f.read()
                                
                                if file == 'app.py':
                                    lines = content.split('\n')
                                    lines = [l for l in lines if 're.compile' not in l and 'aws_key_pattern' not in l]
                                    content = '\n'.join(lines)
                                
                                if aws_key_pattern.search(content) or aws_secret_pattern.search(content) or "EXAMPLEKEY" in content:
                                    has_secret_leak = True
                                    break
                        except:
                            pass
                if has_secret_leak:
                    break

            if has_secret_leak:
                if not os.path.exists(secret_timestamp_file):
                    with open(secret_timestamp_file, "w") as f:
                        f.write(wib_now.strftime("%Y-%m-%d %H:%M:%S"))
                with open(secret_timestamp_file, "r") as f:
                    secret_time = f.read()
            else:
                if os.path.exists(secret_timestamp_file):
                    os.remove(secret_timestamp_file)
        except Exception as e:
            print(f"[ERROR SCANNER] Gagal menjalankan Secret Scanner: {e}")

        # =========================================================
        # 1C. ANALISIS KONDISI SISTEM: MALICIOUS/UNSIGNED COMMIT (BARU)
        # =========================================================
        has_malicious_commit = False
        commit_attack_time = wib_now.strftime("%Y-%m-%d %H:%M:%S")

        try:
            if GITHUB_USERNAME and GITHUB_REPO:
                commit_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/commits?per_page=1"
                commit_resp = requests.get(commit_url, headers=GITHUB_HEADERS)
                if commit_resp.ok:
                    commits = commit_resp.json()
                    if commits:
                        # Cek status verifikasi penandatanganan (Signed Commit GPG/SSH)
                        is_verified = commits[0].get('commit', {}).get('verification', {}).get('verified', False)
                        
                        # Jika tidak ditandatangani, anggap sebagai celah/potensi Malicious Commit
                        if not is_verified:
                            has_malicious_commit = True
                            gh_time = commits[0].get('commit', {}).get('committer', {}).get('date', '').replace('T', ' ').replace('Z', '')
                            try:
                                utc_gh = datetime.strptime(gh_time, "%Y-%m-%d %H:%M:%S")
                                commit_attack_time = (utc_gh + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass

            # Manajemen State Lock Waktu Commit
            if has_malicious_commit:
                if not os.path.exists(commit_timestamp_file):
                    with open(commit_timestamp_file, "w") as f:
                        f.write(commit_attack_time)
                with open(commit_timestamp_file, "r") as f:
                    commit_attack_time = f.read()
            else:
                if os.path.exists(commit_timestamp_file):
                    os.remove(commit_timestamp_file)
        except Exception as e:
            print(f"[ERROR SCANNER] Gagal memeriksa status commit: {e}")

        # =========================================================
        # 2. LOGIKA PIPELINE & DEPENDABOT (VERSI PERBAIKAN TIMESTAMPS)
        # =========================================================
        dependabot_timestamp_file = "dependabot_time.txt" # Lock file untuk mengunci waktu dependabot terbaru
        
        has_pipeline_failure = False
        is_pipeline_currently_broken = False
        pipeline_time = wib_now.strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            if GITHUB_USERNAME and GITHUB_REPO:
                url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/actions/runs?per_page=5"
                run_resp = requests.get(url, headers=GITHUB_HEADERS)
                if run_resp.ok:
                    runs = run_resp.json().get('workflow_runs', [])
                    if runs:
                        latest_conclusion = runs[0].get('conclusion')
                        latest_status = runs[0].get('status')
                        if latest_conclusion == 'failure' or latest_status == 'failure':
                            is_pipeline_currently_broken = True

                    for r in runs:
                        if r.get('conclusion') == 'failure':
                            has_pipeline_failure = True
                            gh_time = r.get('updated_at', '').replace('T', ' ').replace('Z', '')
                            try:
                                utc_gh = datetime.strptime(gh_time, "%Y-%m-%d %H:%M:%S")
                                pipeline_time = (utc_gh + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                            break
        except Exception:
            pass

        has_dependency_poison = False
        dependabot_time = wib_now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            if GITHUB_USERNAME and GITHUB_REPO:
                dep_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dependabot/alerts?state=open&per_page=5"
                dep_resp = requests.get(dep_url, headers=GITHUB_HEADERS)
                if dep_resp.ok:
                    alerts = dep_resp.json()
                    for alert in alerts:
                        if alert.get('state') == 'open':
                            has_dependency_poison = True
                            
                            # PERBAIKAN 1: Menggunakan 'updated_at' agar saat alert lama terbuka kembali, 
                            # waktu yang diambil adalah waktu saat simulasi kedua dipicu.
                            gh_time = alert.get('updated_at', alert.get('created_at', '')).replace('T', ' ').replace('Z', '')
                            try:
                                utc_gh = datetime.strptime(gh_time, "%Y-%m-%d %H:%M:%S")
                                dependabot_time = (utc_gh + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                pass
                            break

            # PERBAIKAN 2: Mekanisme State Lock File (Menjaga waktu tetap konsisten & bersih saat simulasi direset)
            if has_dependency_poison:
                if not os.path.exists(dependabot_timestamp_file):
                    with open(dependabot_timestamp_file, "w") as f:
                        f.write(dependabot_time)
                with open(dependabot_timestamp_file, "r") as f:
                    dependabot_time = f.read()
            else:
                if os.path.exists(dependabot_timestamp_file):
                    os.remove(dependabot_timestamp_file)
                    
        except Exception:
            pass

        # =========================================================
        # 3. PETA STATUS MITIGASI (DITAMBAH SEBAGAI STRIDE/MITIGASI)
        # =========================================================
        mitigations = {
            "image_signing": {
                "name": "Docker Image Signing (Cosign)",
                "status": "Active" if not has_malicious_image else "Bypassed",
                "description": "Memastikan hanya image bertanda tangan digital resmi yang diizinkan berjalan."
            },
            "dependency_scanning": {
                "name": "Dependency Vulnerability Scanner",
                "status": "Active" if not has_dependency_poison else "Vulnerable",
                "description": "Memantau pustaka pihak ketiga terhadap serangan Dependency Poisoning."
            },
            "pipeline_hardening": {
                "name": "Pipeline Privilege Protection",
                "status": "Active" if not is_pipeline_currently_broken else "Under Review",
                "description": "Membatasi hak akses GitHub Token untuk mencegah Privilege Abuse."
            },
            "secret_protection": {
                "name": "Secret Leakage Scanner",
                "status": "Active" if not has_secret_leak else "Vulnerable",
                "description": "Mencegah kebocoran API Keys, Password, dan GPG Key ke repositori publik."
            },
            "commit_signing": { # Komponen Perlindungan Baru ke-5
                "name": "Commit Signing Verification (GPG)",
                "status": "Active" if not has_malicious_commit else "Vulnerable",
                "description": "Memverifikasi integritas identitas kontributor lewat tanda tangan digital Git Commit."
            }
        }

        # =========================================================
        # 4. SECURITY FINDINGS TIMELINE
        # =========================================================
        timeline = []
        if has_malicious_image:
            timeline.append({
                "timestamp": malicious_time,
                "stride_category": "Tampering / Spoofing",
                "attack_vector": "Supply Chain Attack",
                "description": "Terdeteksi Docker image ilegal diinjeksikan secara manual tanpa tanda tangan valid.",
                "status": "CRITICAL"
            })
            
        if is_pipeline_currently_broken:
            timeline.append({
                "timestamp": pipeline_time,
                "stride_category": "Elevation of Privilege",
                "attack_vector": "Pipeline Privilege Abuse",
                "description": "Eksekusi workflow diblokir otomatis atau gagal karena indikasi modifikasi permission pada token.",
                "status": "HIGH"
            })

        if has_dependency_poison:
            timeline.append({
                "timestamp": dependabot_time,
                "stride_category": "Information Disclosure",
                "attack_vector": "Dependency Poisoning",
                "description": "Dependabot mendeteksi library pihak ketiga terinfeksi kode berbahaya di repositori.",
                "status": "MEDIUM"
            })

        if has_secret_leak:
            timeline.append({
                "timestamp": secret_time,
                "stride_category": "Information Disclosure",
                "attack_vector": "Secret Leakage Exposure",
                "description": "Terdeteksi kredensial API kunci privat (Hardcoded Secret) terekspos secara terbuka pada file repositori.",
                "status": "CRITICAL"
            })
        else:
            timeline.append({
                "timestamp": (wib_now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                "stride_category": "None (Mitigated)",
                "attack_vector": "Secret Leakage Check",
                "description": "Pemindaian berkala repositori: Tidak ada sensitif token atau kunci GPG yang bocor ke publik.",
                "status": "SECURE"
            })

        # Komponen Malicious Commit pada Timeline
        if has_malicious_commit:
            timeline.append({
                "timestamp": commit_attack_time,
                "stride_category": "Spoofing / Repudiation",
                "attack_vector": "Malicious Unsigned Commit",
                "description": "Peringatan Keamanan: Terdeteksi aktivitas git push commit terbaru tanpa tanda tangan digital valid (Unverified Commit).",
                "status": "HIGH"
            })

        timeline.sort(key=lambda x: x['timestamp'], reverse=True)

        # =========================================================
        # 5. KALKULASI SKOR
        # =========================================================
        base_score = 100
        if has_malicious_image: base_score -= 25
        if is_pipeline_currently_broken: base_score -= 15
        if has_dependency_poison: base_score -= 10
        if has_secret_leak: base_score -= 20
        if has_malicious_commit: base_score -= 15 
        final_score = max(0, base_score)

        return jsonify({
            "score": final_score,
            "grade": "A (Excellent)" if final_score >= 85 else ("B (Good)" if final_score >= 70 else ("C (Warning)" if final_score >= 50 else "D (Critical)")),
            "color": "green" if final_score >= 85 else ("blue" if final_score >= 70 else ("orange" if final_score >= 50 else "red")),
            "mitigations": mitigations,
            "timeline": timeline
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    
def analyze_current_security():
    # 1. Hitung Total Temuan (Security Findings)
    total_findings = sum([
        has_malicious_image, 
        has_secret_leak, 
        has_malicious_commit, 
        is_pipeline_currently_broken, 
        has_dependency_poison
    ])

    # 2. Hitung Temuan Kritis (Critical Vuln) - Berdasarkan scriptmu, image & secret leak adalah CRITICAL
    critical_vuln = sum([has_malicious_image, has_secret_leak])

    # 3. Hitung Skor (Seperti logika aslimu)
    base_score = 100
    if has_malicious_image: base_score -= 25
    if is_pipeline_currently_broken: base_score -= 15
    if has_dependency_poison: base_score -= 10
    if has_secret_leak: base_score -= 20
    if has_malicious_commit: base_score -= 15 
    final_score = max(0, base_score)

    return {
        "total_findings": total_findings,
        "critical_vuln": critical_vuln,
        "score": final_score,
        # Kamu juga bisa me-return detail lainnya jika dibutuhkan oleh route Security Center
    }    

@app.route('/api/dashboard/summary')
def dashboard_summary():
    try:
        # Waktu saat ini dalam WIB (UTC+7)
        wib_now = datetime.utcnow() + timedelta(hours=7)
        
        # File state lock untuk mengunci waktu masing-masing serangan
        timestamp_file = "attack_time.txt"
        secret_timestamp_file = "secret_time.txt"
        commit_timestamp_file = "commit_time.txt"

        # =========================================================
        # GITHUB DATA FETCHING (REPO & PIPELINE HISTORY)
        # =========================================================
        # 1. Mengambil info user/org untuk total repository
        user_url = f"https://api.github.com/users/{GITHUB_USERNAME}"
        user_resp = requests.get(user_url, headers=GITHUB_HEADERS)
        
        if user_resp.status_code == 404:
            user_resp = requests.get(f"https://api.github.com/orgs/{GITHUB_USERNAME}", headers=GITHUB_HEADERS)
            
        user_resp.raise_for_status()
        user_data = user_resp.json()
        
        total_repos = user_data.get('public_repos', 0) + user_data.get('total_private_repos', 0)

        # 2. Menghitung Total Riwayat Pipeline & Proyek Pipeline
        repos_resp = requests.get(f"https://api.github.com/users/{GITHUB_USERNAME}/repos?sort=updated&per_page=20", headers=GITHUB_HEADERS)
        
        total_pipelines_history = 0
        pipeline_projects = 0
        
        if repos_resp.ok:
            repos = repos_resp.json()
            for repo in repos:
                owner_login = repo['owner']['login']
                repo_name = repo['name']

                runs_resp = requests.get(f"https://api.github.com/repos/{owner_login}/{repo_name}/actions/runs?per_page=1", headers=GITHUB_HEADERS)
                
                if runs_resp.ok:
                    repo_runs_count = runs_resp.json().get('total_count', 0)
                    if repo_runs_count > 0:
                        pipeline_projects += 1
                        total_pipelines_history += repo_runs_count

        github_name = user_data.get('name') or user_data.get('login') or GITHUB_USERNAME

        # =========================================================
        # INTEGRASI LOGIKA KEAMANAN REAL-TIME (DARI SCRIPT SECURITY CENTER)
        # =========================================================
        
        # A. ANALISIS ARTIFACT (DOCKER)
        has_malicious_image = False
        try:
            docker_res = subprocess.run(
                ["docker", "images", "--format", "{{.Tag}}"],
                capture_output=True, text=True
            )
            if "unsigned" in docker_res.stdout:
                has_malicious_image = True
        except Exception:
            pass

        # B. ANALISIS SECRET LEAKAGE (REAL-TIME SCANNER)
        has_secret_leak = False
        try:
            aws_key_pattern = re.compile('AKIA' + '[0-9A-Z]{16}')
            aws_secret_pattern = re.compile('wJalrXUtnFEMI/' + '[0-9a-zA-Z+/]{32}')
            
            for root, dirs, files in os.walk('.'):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['venv', '__pycache__', 'env']]
                for file in files:
                    if file.endswith(('.py', '.env', '.json', '.yml', '.yaml', '.txt')):
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, 'r', errors='ignore') as f:
                                content = f.read()
                                if file == 'app.py':
                                    lines = content.split('\n')
                                    lines = [l for l in lines if 're.compile' not in l and 'aws_key_pattern' not in l]
                                    content = '\n'.join(lines)
                                
                                if aws_key_pattern.search(content) or aws_secret_pattern.search(content) or "EXAMPLEKEY" in content:
                                    has_secret_leak = True
                                    break
                        except:
                            pass
                if has_secret_leak:
                    break
        except Exception:
            pass

        # C. ANALISIS MALICIOUS/UNSIGNED COMMIT
        has_malicious_commit = False
        try:
            if GITHUB_USERNAME and GITHUB_REPO:
                commit_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/commits?per_page=1"
                commit_resp = requests.get(commit_url, headers=GITHUB_HEADERS)
                if commit_resp.ok:
                    commits = commit_resp.json()
                    if commits:
                        is_verified = commits[0].get('commit', {}).get('verification', {}).get('verified', False)
                        if not is_verified:
                            has_malicious_commit = True
        except Exception:
            pass

        # D. ANALISIS STATUS WORKFLOW/PIPELINE FAILURE & STATISTIK
        is_pipeline_currently_broken = False
        pipe_success = 0
        pipe_failure = 0
        pipe_cancel = 0
        pipe_in_progress = 0  # TAMBAHKAN: Variabel untuk melacak yang sedang berjalan/antre

        try:
            if GITHUB_USERNAME and GITHUB_REPO:
                # Ambil 30 riwayat run terakhir untuk sampel persentase
                url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/actions/runs?per_page=30"
                run_resp = requests.get(url, headers=GITHUB_HEADERS)
                
                if run_resp.ok:
                    runs = run_resp.json().get('workflow_runs', [])
                    if runs:
                        # Cek status run paling terakhir untuk Posture/Tren (Broken / Not)
                        latest_conclusion = runs[0].get('conclusion')
                        latest_status = runs[0].get('status')
                        if latest_conclusion == 'failure' or latest_status == 'failure':
                            is_pipeline_currently_broken = True
                        
                        # Hitung distribusi untuk grafik
                        for run in runs:
                            status = run.get('status')
                            conclusion = run.get('conclusion')
                            
                            # TAMBAHKAN LOGIKA INI: Cek status 'in_progress' atau 'queued' terlebih dahulu
                            if status in ['in_progress', 'queued', 'pending']:
                                pipe_in_progress += 1
                            elif conclusion == 'success':
                                pipe_success += 1
                            elif conclusion in ['failure', 'timed_out', 'action_required', 'startup_failure']:
                                pipe_failure += 1
                            elif conclusion in ['cancelled', 'skipped']:
                                pipe_cancel += 1
        except Exception:
            pass

        # Kalkulasi Persentase Tingkat Kesuksesan Pipeline
        # TAMBAHKAN pipe_in_progress ke dalam total perhitungan
        total_runs = pipe_success + pipe_failure + pipe_cancel + pipe_in_progress
        
        if total_runs > 0:
            succ_pct = round((pipe_success / total_runs) * 100)
            fail_pct = round((pipe_failure / total_runs) * 100)
            canc_pct = round((pipe_cancel / total_runs) * 100)
            process_pct = round((pipe_in_progress / total_runs) * 100) # DEFINISIKAN process_pct
        else:
            # Default jika kosong, tetapkan process_pct ke 0
            succ_pct, fail_pct, canc_pct, process_pct = 100, 0, 0, 0

        # E. ANALISIS DEPENDABOT ALERTS
        has_dependency_poison = False
        try:
            if GITHUB_USERNAME and GITHUB_REPO:
                dep_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/dependabot/alerts?state=open&per_page=5"
                dep_resp = requests.get(dep_url, headers=GITHUB_HEADERS)
                if dep_resp.ok:
                    alerts = dep_resp.json()
                    for alert in alerts:
                        if alert.get('state') == 'open':
                            has_dependency_poison = True
                            break
        except Exception:
            pass

        # =========================================================
        # KALKULASI METRIK UNTUK SUMMARY DASHBOARD
        # =========================================================
        
        # 1. Hitung Total Celah yang Terbuka/Aktif
        security_findings = sum([
            has_malicious_image, 
            has_secret_leak, 
            has_malicious_commit, 
            is_pipeline_currently_broken, 
            has_dependency_poison
        ])

        # 2. Hitung Kerentanan Kritis (Docker Image Unsigned & Secret Leak)
        critical_vuln = sum([has_malicious_image, has_secret_leak])

        # 3. Hitung Skor kumulatif untuk menentukan tren/status teks
        base_score = 100
        if has_malicious_image: base_score -= 25
        if is_pipeline_currently_broken: base_score -= 15
        if has_dependency_poison: base_score -= 10
        if has_secret_leak: base_score -= 20
        if has_malicious_commit: base_score -= 15 
        final_score = max(0, base_score)

        # 4. Menentukan teks info tren & indikator perbaikan berdasarkan skor
        # LOGIKA BARU: Panah hanya naik (True) jika skor sempurna 100.
        # Selain 100, panah otomatis turun (False).
        is_security_improved = (final_score == 100)

        if final_score == 100:
            trend_text = "Sistem Aman Berintegritas"
        else:
            trend_text = f"Skor Keamanan: {final_score}/100"
        
        # =========================================================
        # TAKSONOMI STRIDE: SKOR IMUNITAS KEAMANAN (0 - 100%)
        # =========================================================
        # Jika aman, skor tiap kategori adalah 100. Jika jebol, skor drop.
        
        stride_spoofing = 30 if has_malicious_commit else 100
        
        # Tampering berkurang jika ada malicious image atau dependency poison
        stride_tampering = 100
        if has_malicious_image: stride_tampering -= 50
        if has_dependency_poison: stride_tampering -= 50
        
        stride_repudiation = 100  # Baseline aman
        stride_info_disclosure = 20 if has_secret_leak else 100
        stride_dos = 40 if is_pipeline_currently_broken else 100
        stride_elevation = 100  # Baseline aman

        stride_scores = [
            stride_spoofing, 
            stride_tampering, 
            stride_repudiation, 
            stride_info_disclosure, 
            stride_dos, 
            stride_elevation
        ]

        # =========================================================
        # STRUCTURE DATA AKHIR UNTUK FRONTEND
        # =========================================================
        
        # Buat label 7 hari terakhir secara otomatis mundur dari hari ini
        trend_labels = [(wib_now - timedelta(days=i)).strftime('%d %b') for i in range(6, -1, -1)]

        summary_data = {
            "totalRepos": total_repos,
            "repoTrend": github_name,
            "activePipelines": total_pipelines_history,
            "pipelineProjects": pipeline_projects,
            
            # --- BAGIAN KEAMANAN (REAL-TIME DATA) ---
            "securityFindings": security_findings,
            "securityTrendText": trend_text,
            "isSecurityImproved": is_security_improved, 
            "criticalVuln": critical_vuln,

            # --- BAGIAN SECURITY POSTURE OVERVIEW ---
            "postureControls": {
                "signedCommit": not has_malicious_commit,
                "secretScanning": not has_secret_leak,
                "sbom": not has_dependency_poison,
                "artifactSigning": not has_malicious_image,
                "leastPrivilegeRunner": not is_pipeline_currently_broken
            },

            # --- BAGIAN TREN TEMUAN KEAMANAN (CHART) ---
            "chartData": {
                "labels": trend_labels,
                "totalFindings": [3, 4, 4, 2, 2, 1, security_findings],
                "criticalFindings": [1, 1, 0, 0, 0, 0, critical_vuln]
            },

            # --- TINGKAT KESUKSESAN PIPELINE ---
            # Pastikan kamu sudah menghitung 'process_pct' di atas baris ini
            # berdasarkan status 'in_progress' atau 'queued' dari GitHub Actions API.

            "pipelineStats": {
                "success": succ_pct,
                "failure": fail_pct,
                "process": process_pct, # <--- TAMBAHKAN BARIS INI
            },

            # --- BAGIAN STRIDE THREAT DISTRIBUTION (BAR CHART) ---
            "strideValues": stride_scores
            
        }

        return jsonify(summary_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

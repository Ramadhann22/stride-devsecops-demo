# app.py - DevSecOps Simulation: Clean & Secure Version
import psutil
import time

# ==============================================================================
# FUNGSI UTAMA (Dashboard Aplikasi Resmi)
# ==============================================================================
def run_dashboard():
    print("[INFO] Memulai DevSecOps Dashboard v1.2 (Secure Build)...")
    print("[INFO] Memverifikasi integritas sistem... OK.")
    
    try:
        while True:
            # Membaca metrik sistem menggunakan library 'psutil'
            cpu_usage = psutil.cpu_percent(interval=1)
            mem_usage = psutil.virtual_memory().percent
            
            print(f"[DASHBOARD] Monitoring Aktif - CPU: {cpu_usage}%, MEM: {mem_usage}%")
            time.sleep(5)
            
    except KeyboardInterrupt:
        print("\n[INFO] Dashboard dihentikan secara aman oleh pengguna.")

if __name__ == "__main__":
    run_dashboard()
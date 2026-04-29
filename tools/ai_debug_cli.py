#!/usr/bin/env python3
import argparse
import glob
import json
import os
import platform
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path


RELEVANT_SDKCONFIG_PREFIXES = (
    "CONFIG_IDF_TARGET",
    "CONFIG_COMPILER_OPTIMIZATION",
    "CONFIG_ESPTOOLPY_FLASH",
    "CONFIG_ESP_WIFI",
    "CONFIG_SPIRAM",
    "CONFIG_LOG_",
    "CONFIG_FREERTOS_HZ",
    "CONFIG_PARTITION_TABLE",
    "CONFIG_LWIP_",
    "CONFIG_MBEDTLS_",
)

RUNTIME_PROBE_RB = r'''# PicoRuby ESP32 runtime probe for LLM-guided optimization.
# Paste this into the PicoRuby shell or save it as /home/ai_runtime_probe.rb and load it.
require 'machine'

def ai_probe_now_us
  Machine.uptime_us
rescue
  Machine.board_millis * 1000
end

def ai_probe_emit(key, value)
  puts "AI_PROBE #{key}=#{value}"
end

def ai_probe_measure(name, n)
  GC.start if defined?(GC)
  t0 = ai_probe_now_us
  result = yield
  t1 = ai_probe_now_us
  ai_probe_emit(name + "_iterations", n)
  ai_probe_emit(name + "_us_total", t1 - t0)
  ai_probe_emit(name + "_us_per_iter_x1000", ((t1 - t0) * 1000) / n)
  result
end

ai_probe_emit("marker", "begin")
ai_probe_emit("uptime_us", ai_probe_now_us)
ai_probe_emit("stack_usage", Machine.stack_usage || -1)
ai_probe_emit("task_stack_free", Machine.task_stack_free) if Machine.respond_to?(:task_stack_free)
ai_probe_emit("memory_before", Machine.memory_snapshot) if Machine.respond_to?(:memory_snapshot)
ai_probe_emit("cpu_before", Machine.cpu_snapshot) if Machine.respond_to?(:cpu_snapshot)

n = 10000
ai_probe_measure("empty_loop", n) do
  i = 0
  while i < n
    i += 1
  end
end

ai_probe_measure("integer_add", n) do
  i = 0
  x = 0
  while i < n
    x += i
    i += 1
  end
  x
end

def ai_probe_method(x)
  x + 1
end

ai_probe_measure("method_call", n) do
  i = 0
  x = 0
  while i < n
    x = ai_probe_method(x)
    i += 1
  end
  x
end

ai_probe_measure("small_string_alloc", 1000) do
  i = 0
  while i < 1000
    s = "probe-" + i.to_s
    i += 1
  end
end

ai_probe_measure("small_array_alloc", 1000) do
  i = 0
  while i < 1000
    a = [i, i + 1, i + 2]
    i += 1
  end
end

ai_probe_measure("hash_access", n) do
  h = { :a => 1, :b => 2, :c => 3 }
  i = 0
  x = 0
  while i < n
    x += h[:b]
    i += 1
  end
  x
end

ai_probe_measure("array_push", 1000) do
  a = []
  i = 0
  while i < 1000
    a << i
    i += 1
  end
  a.length
end

ai_probe_measure("block_call", 1000) do
  x = 0
  1000.times do |i|
    x += i
  end
  x
end

ai_probe_measure("exception_raise_rescue", 100) do
  i = 0
  while i < 100
    begin
      raise "probe"
    rescue
    end
    i += 1
  end
end

if defined?(GC)
  t0 = ai_probe_now_us
  GC.start
  ai_probe_emit("gc_start_us", ai_probe_now_us - t0)
end

ai_probe_emit("stack_usage_after", Machine.stack_usage || -1)
ai_probe_emit("task_stack_free_after", Machine.task_stack_free) if Machine.respond_to?(:task_stack_free)
ai_probe_emit("memory_after", Machine.memory_snapshot) if Machine.respond_to?(:memory_snapshot)
ai_probe_emit("cpu_after", Machine.cpu_snapshot) if Machine.respond_to?(:cpu_snapshot)
ai_probe_emit("uptime_us_after", ai_probe_now_us)
ai_probe_emit("marker", "end")
'''

RUNTIME_PROBE_IRB_LINES = [
    'puts "AI_PROBE marker=begin"',
    'puts "AI_PROBE uptime_us=#{Machine.uptime_us}"',
    'puts "AI_PROBE stack_usage=#{Machine.stack_usage || -1}"',
    'n=1000',
    't=Machine.uptime_us',
    'i=0',
    'while i<n;i+=1;end',
    'dt=Machine.uptime_us-t',
    'puts "AI_PROBE empty_loop_iterations=#{n}"',
    'puts "AI_PROBE empty_loop_us_total=#{dt}"',
    'puts "AI_PROBE empty_loop_us_per_iter_x1000=#{dt*1000/n}"',
    't=Machine.uptime_us',
    'i=0',
    'x=0',
    'while i<n;x+=i;i+=1;end',
    'dt=Machine.uptime_us-t',
    'puts "AI_PROBE integer_add_iterations=#{n}"',
    'puts "AI_PROBE integer_add_us_total=#{dt}"',
    'puts "AI_PROBE integer_add_us_per_iter_x1000=#{dt*1000/n}"',
    'def ai_probe_method(x);x+1;end',
    't=Machine.uptime_us',
    'i=0',
    'x=0',
    'while i<n;x=ai_probe_method(x);i+=1;end',
    'dt=Machine.uptime_us-t',
    'puts "AI_PROBE method_call_iterations=#{n}"',
    'puts "AI_PROBE method_call_us_total=#{dt}"',
    'puts "AI_PROBE method_call_us_per_iter_x1000=#{dt*1000/n}"',
    'n=200',
    't=Machine.uptime_us',
    'i=0',
    'while i<n;s="probe-"+i.to_s;i+=1;end',
    'dt=Machine.uptime_us-t',
    'puts "AI_PROBE small_string_alloc_iterations=#{n}"',
    'puts "AI_PROBE small_string_alloc_us_total=#{dt}"',
    'puts "AI_PROBE small_string_alloc_us_per_iter_x1000=#{dt*1000/n}"',
    't=Machine.uptime_us',
    'i=0',
    'while i<n;a=[i,i+1,i+2];i+=1;end',
    'dt=Machine.uptime_us-t',
    'puts "AI_PROBE small_array_alloc_iterations=#{n}"',
    'puts "AI_PROBE small_array_alloc_us_total=#{dt}"',
    'puts "AI_PROBE small_array_alloc_us_per_iter_x1000=#{dt*1000/n}"',
    't=Machine.uptime_us',
    'GC.start',
    'puts "AI_PROBE gc_start_us=#{Machine.uptime_us-t}"',
    'puts "AI_PROBE marker=end"',
]


def run(cmd, cwd=None, timeout=10):
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return {
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
    except Exception as exc:
        return {"command": cmd, "error": str(exc)}


def repo_root(start):
    result = run(["git", "rev-parse", "--show-toplevel"], cwd=start)
    if result.get("returncode") == 0 and result.get("stdout"):
        return Path(result["stdout"]).resolve()
    return Path(start).resolve()


def file_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return None


def parse_sdkconfig(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith(RELEVANT_SDKCONFIG_PREFIXES):
            values[key] = value.strip('"')
    return dict(sorted(values.items()))


def parse_build_config(path):
    gems = []
    defines = []
    if not path.exists():
        return {"path": str(path), "exists": False, "gems": gems, "defines": defines}
    text = path.read_text(errors="replace")
    for match in re.finditer(r"conf\.gem\s+(?:core:\s+|gemdir:\s+)?['\"]([^'\"]+)['\"]", text):
        gems.append(match.group(1))
    for match in re.finditer(r"conf\.cc\.defines\s*<<\s*['\"]([^'\"]+)['\"]", text):
        defines.append(match.group(1))
    return {
        "path": str(path),
        "exists": True,
        "gems": gems,
        "defines": defines,
        "gemboxes": re.findall(r"conf\.gembox\s+['\"]([^'\"]+)['\"]", text),
    }


def parse_component_cmake(path):
    if not path.exists():
        return {"path": str(path), "exists": False}
    text = path.read_text(errors="replace")
    return {
        "path": str(path),
        "exists": True,
        "picorb_vm_default": re.search(r'set\(PICORB_VM\s+"([^"]+)"', text).group(1)
        if re.search(r'set\(PICORB_VM\s+"([^"]+)"', text)
        else None,
        "source_count": len(re.findall(r"\$\{COMPONENT_DIR\}/[^)\s]+\.c", text)),
        "requires": sorted(set(re.findall(r"^\s+(esp_[a-z0-9_]+|nvs_flash|spi_flash)\s*$", text, re.MULTILINE))),
        "definitions": sorted(set(re.findall(r"-(D[A-Za-z0-9_=.]+)", text))),
    }


def serial_ports():
    ports = sorted(set(glob.glob("/dev/cu.*") + glob.glob("/dev/tty.*")))
    usb_like = [p for p in ports if re.search(r"(usb|serial|wch|slab|jtag)", p, re.I)]
    return {"all": ports, "usb_like": usb_like}


def usb_registry():
    if platform.system() != "Darwin":
        return None
    result = run(["ioreg", "-p", "IOUSB", "-l", "-w", "0"], timeout=15)
    text = result.get("stdout", "")
    devices = []
    current = {}
    for line in text.splitlines():
        name_match = re.search(r"\+-o\s+(.+?)\s+<class IOUSBHostDevice", line)
        if name_match:
            if current:
                devices.append(current)
            current = {"name": name_match.group(1)}
        elif current:
            for key in ("USB Product Name", "idVendor", "idProduct", "locationID", "USB Address"):
                if f'"{key}"' in line:
                    value = line.split("=", 1)[1].strip().strip('"') if "=" in line else ""
                    current[key] = value
    if current:
        devices.append(current)
    return devices


def build_artifacts(build_dir):
    if not build_dir.exists():
        return {"path": str(build_dir), "exists": False}
    patterns = ["*.elf", "*.bin", "*.map", "esp-idf/**/*.a"]
    files = []
    for pattern in patterns:
        for path in build_dir.glob(pattern):
            files.append({"path": str(path), "bytes": file_size(path)})
    top_archives = sorted(
        [f for f in files if f["path"].endswith(".a")],
        key=lambda item: item["bytes"] or 0,
        reverse=True,
    )[:30]
    return {
        "path": str(build_dir),
        "exists": True,
        "primary": [f for f in files if not f["path"].endswith(".a")],
        "top_archives": top_archives,
        "map_hotspots": parse_map_hotspots(next(iter(build_dir.glob("*.map")), None)),
    }


def parse_map_hotspots(map_path):
    if not map_path or not map_path.exists():
        return []
    hotspots = {}
    pattern = re.compile(r"^\s*\.(text|rodata|data|bss)[.\w]*\s+0x[0-9a-fA-F]+\s+0x([0-9a-fA-F]+)\s+(.+)$")
    for line in map_path.read_text(errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        section, size_hex, owner = match.groups()
        size = int(size_hex, 16)
        if size == 0:
            continue
        owner = owner.strip()
        hotspots.setdefault(owner, {"owner": owner, "bytes": 0, "sections": {}})
        hotspots[owner]["bytes"] += size
        hotspots[owner]["sections"][section] = hotspots[owner]["sections"].get(section, 0) + size
    return sorted(hotspots.values(), key=lambda item: item["bytes"], reverse=True)[:30]


def idf_size(project_dir, build_dir):
    if not shutil.which("idf.py"):
        return {"available": False, "reason": "idf.py not found in PATH"}
    return {
        "available": True,
        "size": run(["idf.py", "-B", str(build_dir), "size"], cwd=project_dir, timeout=60),
        "size_components": run(["idf.py", "-B", str(build_dir), "size-components"], cwd=project_dir, timeout=60),
    }


def risk_diagnostics(sdk_values, component):
    risks = []
    def add(level, key, message):
        risks.append({"level": level, "key": key, "message": message})

    if sdk_values.get("CONFIG_COMPILER_OPTIMIZATION_DEBUG") == "y":
        add("high", "debug_optimization", "Debug optimization is enabled; use size/perf optimization for real measurements.")
    if sdk_values.get("CONFIG_COMPILER_OPTIMIZATION_ASSERTIONS_ENABLE") == "y":
        add("medium", "assertions", "Assertions are enabled and add ROM/CPU overhead.")
    if sdk_values.get("CONFIG_ESPTOOLPY_FLASHMODE") == "dio":
        add("medium", "flash_mode", "Flash mode is DIO; QIO can improve fetch speed if the board is stable.")
    if sdk_values.get("CONFIG_ESPTOOLPY_FLASHFREQ") == "40m":
        add("medium", "flash_freq", "Flash frequency is 40MHz; 80MHz can improve code/rodata fetch if stable.")
    if sdk_values.get("CONFIG_ESP_WIFI_ENABLED") == "y":
        add("info", "wifi_enabled", "Wi-Fi is enabled; buffers, WPA supplicant, lwIP, and mbedTLS are major RAM/ROM costs.")
    if sdk_values.get("CONFIG_ESP_WIFI_ENTERPRISE_SUPPORT") == "y":
        add("medium", "wifi_enterprise", "Enterprise Wi-Fi is enabled and often unnecessary for shell/dev boards.")
    if sdk_values.get("CONFIG_ESP_WIFI_ENABLE_WPA3_SAE") == "y":
        add("medium", "wifi_wpa3", "WPA3/SAE support is enabled and increases crypto/supplicant size.")
    if sdk_values.get("CONFIG_MBEDTLS_CERTIFICATE_BUNDLE_DEFAULT_FULL") == "y":
        add("high", "mbedtls_cert_bundle", "Full certificate bundle is enabled; this is large if HTTPS is not required.")
    if sdk_values.get("CONFIG_MBEDTLS_SSL_IN_CONTENT_LEN") == "16384":
        add("medium", "mbedtls_in_buffer", "TLS input buffer is 16KB; smaller values can save RAM for constrained workloads.")
    if "esp_wifi" in component.get("requires", []):
        add("info", "component_requires_wifi", "picoruby-esp32 always requires esp_wifi in CMake; make it conditional for no-Wi-Fi profiles.")
    if "esp_psram" in component.get("requires", []):
        add("info", "component_requires_psram", "picoruby-esp32 requires esp_psram even when PSRAM is not central to the profile.")
    return risks


def load_report(path):
    with open(path, "r") as f:
        return json.load(f)


def compare_reports(base_path, candidate_path):
    base = load_report(base_path)
    cand = load_report(candidate_path)

    def primary_map(report):
        result = {}
        for item in report.get("build", {}).get("artifacts", {}).get("primary", []):
            result[Path(item["path"]).suffix or item["path"]] = item.get("bytes") or 0
        return result

    def archive_map(report):
        result = {}
        for item in report.get("build", {}).get("artifacts", {}).get("top_archives", []):
            result[Path(item["path"]).name] = item.get("bytes") or 0
        return result

    base_primary = primary_map(base)
    cand_primary = primary_map(cand)
    base_archives = archive_map(base)
    cand_archives = archive_map(cand)
    keys = sorted(set(base_primary) | set(cand_primary))
    archive_keys = sorted(set(base_archives) | set(cand_archives))
    return {
        "schema": "picoruby-esp32-ai-debug-compare/v1",
        "base": base_path,
        "candidate": candidate_path,
        "primary": [
            {"key": key, "base": base_primary.get(key, 0), "candidate": cand_primary.get(key, 0), "delta": cand_primary.get(key, 0) - base_primary.get(key, 0)}
            for key in keys
        ],
        "archives": sorted(
            [
                {"key": key, "base": base_archives.get(key, 0), "candidate": cand_archives.get(key, 0), "delta": cand_archives.get(key, 0) - base_archives.get(key, 0)}
                for key in archive_keys
            ],
            key=lambda item: abs(item["delta"]),
            reverse=True,
        )[:30],
    }


def run_serial_probe(port, baudrate, timeout_sec):
    def send_line(fd, line):
        for ch in line:
            os.write(fd, ch.encode("utf-8"))
            time.sleep(0.003)
        os.write(fd, b"\r\n")
        time.sleep(0.5)

    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        baud = getattr(termios, f"B{baudrate}", termios.B115200)
        attrs[4] = baud
        attrs[5] = baud
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        time.sleep(8.0)
        send_line(fd, "ai_probe")

        deadline = time.time() + timeout_sec
        raw = bytearray()
        while time.time() < deadline:
            readable, _, _ = select.select([fd], [], [], 0.2)
            if not readable:
                continue
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            if chunk:
                raw.extend(chunk)
                if b"AI_PROBE marker=end" in raw:
                    break
        text = raw.decode("utf-8", errors="replace")
        metrics = {}
        boot = []
        for line in text.splitlines():
            if line.startswith("AI_PROBE "):
                payload = line[len("AI_PROBE "):]
                if "=" in payload:
                    key, value = payload.split("=", 1)
                    metrics[key] = value
            elif line.startswith("AI_BOOT ") or line.startswith("AI_MEM ") or line.startswith("AI_CPU "):
                boot.append(line)
        return {"port": port, "baudrate": baudrate, "metrics": metrics, "boot": boot, "raw": text}
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_attrs)
        os.close(fd)


def collect(args):
    root = repo_root(Path.cwd())
    project_dir = Path(args.project_dir).resolve() if args.project_dir else root / "tools/ruby_debug_ide/esp_project"
    build_dir = Path(args.build_dir).resolve() if args.build_dir else project_dir / "build"
    sdkconfig = Path(args.sdkconfig).resolve() if args.sdkconfig else project_dir / "sdkconfig"

    component_cmake = parse_component_cmake(root / "CMakeLists.txt")
    sdk_values = parse_sdkconfig(sdkconfig)
    report = {
        "schema": "picoruby-esp32-ai-debug-cli/v1",
        "repo": {
            "root": str(root),
            "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root).get("stdout"),
            "status": run(["git", "status", "--porcelain"], cwd=root).get("stdout"),
            "submodules": run(["git", "submodule", "status", "--recursive"], cwd=root).get("stdout"),
        },
        "host": {
            "system": platform.platform(),
            "python": sys.version.split()[0],
            "executables": {name: shutil.which(name) for name in ("ruby", "rake", "cmake", "ninja", "idf.py", "mise")},
        },
        "device": {
            "serial_ports": serial_ports(),
            "usb_registry": usb_registry(),
        },
        "build": {
            "project_dir": str(project_dir),
            "build_dir": str(build_dir),
            "sdkconfig": {"path": str(sdkconfig), "values": sdk_values},
            "artifacts": build_artifacts(build_dir),
            "idf_size": idf_size(project_dir, build_dir) if args.run_idf_size else None,
        },
        "picoruby_component": {
            "cmake": component_cmake,
            "femtoruby_xtensa": parse_build_config(root / "build_config/xtensa-esp-femtoruby.rb"),
            "picoruby_xtensa": parse_build_config(root / "build_config/xtensa-esp-picoruby.rb"),
            "entrypoint": str(root / "picoruby-esp32.c"),
            "main_task": str(root / "mrblib/main_task.rb"),
        },
        "llm_hints": [
            "Compare idf.py size / size-components before and after each feature flag change.",
            "For shell/development builds, keep shell enabled but measure mbedTLS/socket/Wi-Fi and peripheral costs separately.",
            "If CONFIG_SPIRAM is enabled, verify whether Ruby VM heap in PSRAM is acceptable for CPU-bound workloads.",
            "Review top_archives and sdkconfig optimization flags before changing Ruby VM internals.",
        ],
        "runtime_probe": {
            "available": True,
            "emit_command": "tools/ai_debug_cli.py --emit-runtime-probe",
            "metrics": [
                "Ruby loop overhead",
                "Integer arithmetic overhead",
                "Method dispatch overhead",
                "Small String allocation overhead",
                "Small Array allocation overhead",
                "GC.start latency",
                "Machine.stack_usage before/after",
                "Machine.memory_snapshot before/after when firmware includes it",
                "Machine.cpu_snapshot before/after when firmware includes it",
            ],
            "output_prefix": "AI_PROBE",
        },
        "risk_diagnostics": risk_diagnostics(sdk_values, component_cmake),
    }
    return report


def print_text(report):
    print("# PicoRuby ESP32 AI debug report")
    print(f"repo: {report['repo']['root']}")
    print(f"branch: {report['repo']['branch']}")
    print(f"dirty: {'yes' if report['repo']['status'] else 'no'}")
    print("\n## Device")
    print("usb-like serial ports:")
    for port in report["device"]["serial_ports"]["usb_like"]:
        print(f"- {port}")
    print("\n## Build")
    print(f"project_dir: {report['build']['project_dir']}")
    print(f"build_dir: {report['build']['build_dir']}")
    for key, value in report["build"]["sdkconfig"]["values"].items():
        print(f"{key}={value}")
    print("\n## Artifacts")
    artifacts = report["build"]["artifacts"]
    for item in artifacts.get("primary", []):
        print(f"- {item['bytes']:>10} {item['path']}")
    print("\n## Top archives")
    for item in artifacts.get("top_archives", [])[:15]:
        print(f"- {item['bytes']:>10} {item['path']}")
    print("\n## Map hotspots")
    for item in artifacts.get("map_hotspots", [])[:10]:
        print(f"- {item['bytes']:>10} {item['owner']}")
    print("\n## PicoRuby component")
    cmake = report["picoruby_component"]["cmake"]
    print(f"PICORB_VM default: {cmake.get('picorb_vm_default')}")
    print(f"C source count: {cmake.get('source_count')}")
    print(f"Requires: {', '.join(cmake.get('requires', []))}")
    print("\n## LLM hints")
    for hint in report["llm_hints"]:
        print(f"- {hint}")
    print("\n## Runtime probe")
    print(f"emit: {report['runtime_probe']['emit_command']}")
    print(f"prefix: {report['runtime_probe']['output_prefix']}")
    print("\n## Risk diagnostics")
    for item in report.get("risk_diagnostics", []):
        print(f"- [{item['level']}] {item['key']}: {item['message']}")


def main():
    parser = argparse.ArgumentParser(description="Collect PicoRuby ESP32 debug data for LLM-guided optimization.")
    parser.add_argument("--project-dir", help="ESP-IDF project directory. Defaults to tools/ruby_debug_ide/esp_project.")
    parser.add_argument("--build-dir", help="ESP-IDF build directory. Defaults to PROJECT_DIR/build.")
    parser.add_argument("--sdkconfig", help="sdkconfig path. Defaults to PROJECT_DIR/sdkconfig.")
    parser.add_argument("--run-idf-size", action="store_true", help="Run idf.py size commands if idf.py is available.")
    parser.add_argument("--emit-runtime-probe", action="store_true", help="Print a Ruby runtime probe script for PicoRuby shell.")
    parser.add_argument("--run-probe", action="store_true", help="Send the runtime probe to a PicoRuby shell over serial and collect AI_PROBE output.")
    parser.add_argument("--port", help="Serial port for --run-probe. Defaults to first USB-like serial port.")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--probe-timeout", type=float, default=30.0)
    parser.add_argument("--compare", nargs=2, metavar=("BASE_JSON", "CANDIDATE_JSON"), help="Compare two JSON reports produced by this CLI.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", help="Write report to this file instead of stdout.")
    args = parser.parse_args()

    if args.emit_runtime_probe:
        output = RUNTIME_PROBE_RB
        if args.output:
            Path(args.output).write_text(output)
        else:
            print(output, end="")
        return

    if args.compare:
        result = compare_reports(args.compare[0], args.compare[1])
        output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            Path(args.output).write_text(output)
        else:
            print(output, end="")
        return

    if args.run_probe:
        port = args.port
        if not port:
            candidates = serial_ports()["usb_like"]
            if not candidates:
                raise SystemExit("No USB-like serial port found. Pass --port explicitly.")
            port = candidates[0]
        result = run_serial_probe(port, args.baudrate, args.probe_timeout)
        output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            Path(args.output).write_text(output)
        else:
            print(output, end="")
        return

    report = collect(args)
    if args.format == "json":
        output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    else:
        from io import StringIO

        old_stdout = sys.stdout
        buffer = StringIO()
        sys.stdout = buffer
        try:
            print_text(report)
        finally:
            sys.stdout = old_stdout
        output = buffer.getvalue()

    if args.output:
        Path(args.output).write_text(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()

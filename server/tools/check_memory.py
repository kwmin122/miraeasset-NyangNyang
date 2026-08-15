"""다이어트가 실제로 걸렸는지 확인한다.

레포를 pull만 받으면 server/artifacts/ 산출물 두 개가 없다. 용량이 커서
git에서 제외했기 때문이다(sqlite 1.19GB, faiss 527MB, GitHub 파일 한도 100MB).
산출물이 없으면 search.py가 원본 경로로 조용히 폴백한다. 에러가 안 나서
'다이어트가 걸린 줄 알고' 넘어가기 쉬우므로 이 스크립트가 대신 확인한다.

    python server/tools/check_memory.py

확인 항목
  1. 산출물 두 개가 있는가 (없으면 만드는 명령을 알려준다)
  2. 서버와 같은 경로로 검색기를 올렸을 때 최대 메모리가 목표(4GB) 안인가

의존성은 표준 라이브러리만 쓴다. psutil이 venv에 없어서 넣지 않았다.
"""
import ctypes
import os
import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

TARGET_GB = 4.0
ARTIFACTS = {
    "chunk_meta.sqlite": "python server/tools/build_chunk_meta_sqlite.py",
    "index_sq8.faiss": "python server/tools/build_sq8_index.py",
}


def rss_bytes():
    """이 프로세스가 지금까지 쓴 메모리 최대치(peak RSS).

    현재값이 아니라 최대치를 본다. 4GB 컨테이너가 죽었던 원인이 임베딩 모델
    가중치를 올리는 1초 동안의 스파이크(651MB -> 3.4GB)였기 때문이다.
    현재값만 재면 그 순간이 지나간 뒤라 '통과'가 나오는데 실제로는 죽는다.
    OS마다 읽는 곳이 달라 셋을 다 둔다.
    """
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong),
                        ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        c = Counters()
        c.cb = ctypes.sizeof(c)
        h = ctypes.windll.kernel32.GetCurrentProcess()
        for dll, fn in ((ctypes.windll.kernel32, "K32GetProcessMemoryInfo"),
                        (ctypes.windll.psapi, "GetProcessMemoryInfo")):
            f = getattr(dll, fn, None)
            if f and f(ctypes.c_void_p(h), ctypes.byref(c), ctypes.c_ulong(c.cb)):
                return c.PeakWorkingSetSize
        return 0
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    import resource
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return ru if sys.platform == "darwin" else ru * 1024


def gb(n):
    return n / (1024 ** 3)


def check_artifacts():
    out = SERVER / "artifacts"
    missing = []
    print("[1/2] 다이어트 산출물")
    for name, cmd in ARTIFACTS.items():
        p = out / name
        if p.exists():
            print("  있음   %-20s %6.0f MB" % (name, p.stat().st_size / 1024 ** 2))
        else:
            print("  없음   %-20s  -> %s" % (name, cmd))
            missing.append(cmd)
    return missing


def main():
    missing = check_artifacts()
    if missing:
        print("\n산출물이 없다. 아래를 먼저 돌려라 (한 번만 만들면 된다).")
        for cmd in missing:
            print("  " + cmd)
        print("\n만들지 않으면 원본 경로로 폴백해서 다이어트가 걸리지 않는다.")
        return 1

    print("\n[2/2] 메모리 실측 (서버와 같은 경로로 적재)")
    base = rss_bytes()
    t0 = time.time()

    from app.search import get_searcher
    s = get_searcher()
    print("  검색기 적재      최대 %5.2f GB  (+%.2f)" % (gb(rss_bytes()), gb(rss_bytes() - base)))

    os.environ.setdefault("AGENT_MODE", "sunwoo")
    from app.sunwoo_agent import SunwooAgent  # noqa: F401
    print("  선우 파이프라인  최대 %5.2f GB" % gb(rss_bytes()))

    s.search("자기주식 취득 결정", 5)
    peak = rss_bytes()
    print("  검색 1회 후      최대 %5.2f GB   (%.1f초)" % (gb(peak), time.time() - t0))

    ok = gb(peak) < TARGET_GB
    print("\n%s  %.2f GB / 목표 %.1f GB" % ("통과" if ok else "초과", gb(peak), TARGET_GB))
    if not ok:
        print("초과했다. artifacts가 최신인지, search.py 몽키패치가 걸렸는지 확인해라.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

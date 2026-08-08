from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_runtime_image_is_cpu_only_and_preloads_embedding_model() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "download.pytorch.org/whl/cpu" in dockerfile
    assert 'torch==2.7.1+cpu' in dockerfile
    assert "SentenceTransformer('/app/models/bge-small-zh-v1.5'" in dockerfile
    assert "HF_HUB_OFFLINE=1" in dockerfile
    assert "nvidia" not in dockerfile.lower()


def test_frontend_has_agent_subpath_contract() -> None:
    vite = (ROOT / "frontend" / "vite.config.ts").read_text()
    main = (ROOT / "frontend" / "src" / "main.tsx").read_text()

    assert "base: '/agent/'" in vite
    assert 'window.location.pathname.startsWith("/agent")' in main
    assert "globalThis.crypto?.randomUUID" in main
    assert 'return "web-" + Date.now().toString(36)' in main
    assert 'fetch(apiUrl("/api/profile"))' in main

from pathlib import Path
import yaml
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config" / "sources.yaml"


def load_sources():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"File non trovato: {CONFIG_FILE}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("sources.yaml non contiene un oggetto YAML valido")

    sources = data.get("sources")

    if not isinstance(sources, list):
        raise ValueError("La chiave 'sources' non contiene una lista valida")

    return sources


def test_sources():
    sources = load_sources()

    print(f"Sorgenti configurate: {len(sources)}")

    for source in sources:
        name = source.get("name", "Sconosciuto")
        url = source.get("url", "")

        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()

            size_kb = len(response.text) / 1024
            print(f"[OK] {name}: {size_kb:.1f} KB")

        except Exception as exc:
            print(f"[ERRORE] {name}: {exc}")


if __name__ == "__main__":
    test_sources()

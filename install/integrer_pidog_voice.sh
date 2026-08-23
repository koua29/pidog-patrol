#!/usr/bin/env bash
#
# Branche pidog-patrol sur pidog-voice : « PiDog, patrouille » lance ce projet.
#
#   ./install/integrer_pidog_voice.sh [chemin-vers-pidog-voice]
#   ./install/integrer_pidog_voice.sh --retirer
#
# Remplace la séquence de la commande `patrouille` par un appel externe.
# La patrouille intégrée à pidog-voice était volontairement rudimentaire ;
# celle-ci gère les capteurs, la caméra et l'IA.
set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOICE="${2:-${1:-$HOME/script/pidog-voice}}"
[ "${1:-}" = "--retirer" ] && VOICE="${2:-$HOME/script/pidog-voice}"
CFG="$VOICE/commandes.json"

[ -f "$CFG" ] || { echo "!! commandes.json introuvable dans $VOICE"; exit 1; }
cp "$CFG" "$CFG.avant-patrol"

python3 - "$CFG" "$RACINE/patrol.py" "${1:-}" <<'PYEOF'
import json, sys, collections
cfg_path, patrol_py, action = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(cfg_path, encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
pat = d["commandes"]["patrouille"]
if action == "--retirer":
    pat["sequence"] = [{"builtin": "patrouille"}]
    print("patrouille rendue à pidog-voice")
else:
    pat["sequence"] = [collections.OrderedDict([("externe", patrol_py),
                                                ("args", ["--duree", "300"])])]
    pat["intention"] = ("partir explorer, surveiller, faire une ronde, patrouiller "
                        "dans la piece, se deplacer en autonomie")
    print(f"patrouille déléguée à {patrol_py}")
json.dump(d, open(cfg_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PYEOF

echo "sauvegarde : $CFG.avant-patrol"
python3 "$VOICE/outils/verifier_config.py" | tail -2
echo
echo "Redémarrez l'écoute pour prendre en compte le changement :"
echo "  pkill -f '[e]ars_loop'; pkill -f '[p]idog_ears'"
echo "  nohup $VOICE/pi/ears_loop.sh > ~/script/superviseur.log 2>&1 &"

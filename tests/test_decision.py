"""Tests de la logique de decision — aucun materiel requis, tourne sur n'importe quelle machine.

    python3 tests/test_decision.py

Verifie notamment l'invariant de securite : la vision ne peut JAMAIS autoriser
un passage que l'ultrason refuse.
"""
import json, os, sys
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
import patrol

cfg = json.load(open(os.path.join(RACINE, "config.json")))
cfg["vision"]["influence_navigation"] = True

class VisionFactice:
    def __init__(self, avis=None): self._a = avis
    def avis(self, *a, **k): return self._a

def neuve(vision=None):
    p = patrol.Patrouille.__new__(patrol.Patrouille)
    p.cfg, p.vision = cfg, vision or VisionFactice()
    p.blocages, p.distances, p.sans_echo, p.raison_arret = 0, [], 0, None
    p.simulation = True
    return p

ok = 0; total = 0
def verifie(nom, obtenu, attendu):
    global ok, total
    total += 1
    bon = obtenu == attendu
    ok += bon
    print(f"  {'OK ' if bon else 'ECHEC'} {nom:<46} -> {obtenu} {'' if bon else f'(attendu {attendu})'}")

p = neuve(); verifie("voie libre a 160 cm", p.decider(160)[0], "avancer")
p = neuve(); verifie("obstacle a 25 cm", p.decider(25)[0], "gauche")
p = neuve(); verifie("obstacle pile au seuil (39 cm)", p.decider(39)[0], "gauche")
p = neuve(); verifie("juste au-dessus du seuil (41 cm)", p.decider(41)[0], "avancer")

p = neuve()
for i in range(5): r = p.decider(None)[0]
verifie("aucun echo (1er cycle) = avance", neuve().decider(None)[0], "avancer")
p = neuve()
res = [p.decider(None)[0] for _ in range(7)]
verifie("aucun echo prolonge (7 cycles) = tourne", res[-1], "gauche")

p = neuve(); p.distances = [100.0, 100.5, 99.8, 100.2]
verifie("j'avance sans me rapprocher = coince", p.decider(100)[0], "demi_tour")
p = neuve(); p.distances = [160.0, 140.0, 120.0, 100.0]
verifie("j'avance et je me rapproche = normal", p.decider(100)[0], "avancer")

p = neuve(VisionFactice({"voie_libre": False, "conseil": "droite", "scene": "cables au sol"}))
verifie("vision dit non alors que l'ultrason dit oui", p.decider(160)[0], "droite")
p = neuve(VisionFactice({"voie_libre": True, "conseil": "avancer", "scene": "couloir"}))
verifie("vision dit oui mais obstacle a 20 cm", p.decider(20)[0], "gauche")

print(f"\n=> {ok}/{total} tests passes")
sys.exit(0 if ok == total else 1)

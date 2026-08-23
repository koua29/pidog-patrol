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
    p.depuis_balayage, p.derniere_vue = 0, {}
    p.dernier_virage = (None, 0.0)
    p.historique_blocages, p.strategie = [], 0
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

print("\n-- balayage lateral (le cas de la porte qu'on longe en frottant) --")

p = neuve()
verifie("voie libre devant mais mur a 15 cm a gauche",
        p.decider(150, {"gauche_libre":150.0,"gauche_pres":15.0,"centre":150.0,"droite_libre":200.0,"droite_pres":200.0})[0], "droite")
p = neuve()
verifie("voie libre devant mais mur a 12 cm a droite",
        p.decider(150, {"gauche_libre":200.0,"gauche_pres":200.0,"centre":150.0,"droite_libre":150.0,"droite_pres":12.0})[0], "gauche")
p = neuve()
verifie("les deux cotes degages = on avance",
        p.decider(150, {"gauche_libre":120.0,"gauche_pres":120.0,"centre":150.0,"droite_libre":130.0,"droite_pres":130.0})[0], "avancer")
p = neuve()
verifie("obstacle devant : on tourne vers le cote le PLUS libre",
        p.decider(25, {"gauche_libre":40.0,"gauche_pres":40.0,"centre":25.0,"droite_libre":180.0,"droite_pres":180.0})[0], "droite")
p = neuve()
verifie("obstacle devant : cote le plus libre a gauche",
        p.decider(25, {"gauche_libre":190.0,"gauche_pres":190.0,"centre":25.0,"droite_libre":35.0,"droite_pres":35.0})[0], "gauche")
p = neuve()
verifie("obstacle devant, aucun echo a droite = espace libre",
        p.decider(25, {"gauche_libre":60.0,"gauche_pres":60.0,"centre":25.0,"droite_libre":None,"droite_pres":None})[0], "droite")
p = neuve()
verifie("cote pile a la marge (30 cm) = on n'ecarte pas",
        p.decider(150, {"gauche_libre":150.0,"gauche_pres":30.0,"centre":150.0,"droite_libre":150.0,"droite_pres":150.0})[0], "avancer")

print("\n-- recul (le cas du nez dans le mur) --")

p = neuve()
verifie("colle au mur devant (4 cm)",
        p.decider(4, {"gauche_libre":5.5,"gauche_pres":5.5,"centre":4.0,"droite_libre":3.7,"droite_pres":3.7})[0], "reculer")
p = neuve()
verifie("coince : tout est sous 15 cm",
        p.decider(12, {"gauche_libre":10.0,"gauche_pres":10.0,"centre":12.0,"droite_libre":8.0,"droite_pres":8.0})[0], "reculer")
p = neuve()
verifie("proche devant mais un cote degage = on tourne",
        p.decider(12, {"gauche_libre":150.0,"gauche_pres":150.0,"centre":12.0,"droite_libre":9.0,"droite_pres":9.0})[0], "gauche")
p = neuve()
verifie("obstacle a 25 cm = on tourne, pas de recul",
        p.decider(25, {"gauche_libre":40.0,"gauche_pres":40.0,"centre":25.0,"droite_libre":180.0,"droite_pres":180.0})[0], "droite")
p = neuve()
verifie("sans balayage, colle devant = recul quand meme",
        p.decider(6, {})[0], "reculer")

print("\n-- amplitude du balayage (l'issue n'existe qu'a 90 deg) --")

# Cas reel mesure : +90=42, +45=10, 0=5, -45=5, -90=41. La seule issue est
# laterale a 90 deg ; un balayage limite a +/-45 la raterait.
p = neuve()
reel = {"centre": 5.0,
        "gauche_libre": 42.0, "gauche_pres": 10.0,
        "droite_libre": 41.0, "droite_pres": 5.0,
        "brut": {90: 42.0, 45: 10.0, 0: 5.0, -45: 5.0, -90: 41.0}}
verifie("colle devant mais issue vue a 90 deg", p.decider(5, reel)[0], "gauche")

p = neuve()
etroit = {"centre": 5.0, "gauche_libre": 10.0, "gauche_pres": 10.0,
          "droite_libre": 5.0, "droite_pres": 5.0, "brut": {45: 10.0, 0: 5.0, -45: 5.0}}
verifie("meme scene vue a 45 deg seulement = recul", p.decider(5, etroit)[0], "reculer")

print("\n-- anti-oscillation et strategies graduees --")

import time as _t

p = neuve(); p.dernier_virage = ("gauche", _t.time())
verifie("vient de tourner a gauche : ne repart pas a droite",
        p.decider(25, {"gauche_libre":30.0,"gauche_pres":30.0,"centre":25.0,
                       "droite_libre":180.0,"droite_pres":180.0})[0], "gauche")

p = neuve(); p.dernier_virage = ("gauche", _t.time() - 10)
verifie("virage ancien (10 s) : le delai ne s'applique plus",
        p.decider(25, {"gauche_libre":30.0,"gauche_pres":30.0,"centre":25.0,
                       "droite_libre":180.0,"droite_pres":180.0})[0], "droite")

p = neuve(); p.dernier_virage = ("droite", _t.time())
verifie("frottement a droite mais on vient de tourner a droite : on continue",
        p.decider(150, {"gauche_libre":150.0,"gauche_pres":150.0,"centre":150.0,
                        "droite_libre":150.0,"droite_pres":12.0})[0], "avancer")

bloque = {"gauche_libre":8.0,"gauche_pres":8.0,"centre":5.0,
          "droite_libre":7.0,"droite_pres":7.0}
p = neuve()
verifie("1er blocage : strategie « tourner » -> recul", p.decider(5, bloque)[0], "reculer")
p = neuve(); p.historique_blocages = [_t.time()] * 5
verifie("blocages repetes : escalade vers le demi-tour", p.decider(5, bloque)[0], "demi_tour")

print(f"\n=> {ok}/{total} tests passes")
sys.exit(0 if ok == total else 1)

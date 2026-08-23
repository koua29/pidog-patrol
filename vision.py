#!/usr/bin/env python3
"""
Vision de pidog-patrol : décrit la scène via un modèle vision-langage distant.

MESURE AVANT CONCEPTION (23/08/2026, Mac M2 Pro 32 Go) :

    qwen3-vl:8b   ~26 s par image, description juste et detaillee
    moondream:1.8b  4 s, mais reponses tronquees ou vides — inutilisable

26 s, c'est une eternite pour un robot qui marche. La vision ne peut donc PAS
piloter la navigation. Ce module tourne dans un thread separe, a basse cadence,
et la boucle de patrouille ne l'attend jamais. Son role est de RACONTER ce que le
robot voit, et au plus de le rendre prudent — jamais de l'autoriser a avancer.

Piege : qwen3-vl est un modele « a raisonnement ». Avec think=False il renvoie un
contenu VIDE ; avec un budget de tokens trop court, tout part dans le champ
`thinking` et `content` reste vide (done_reason=length). Il lui faut du budget.
"""
import base64
import json
import threading
import time
import urllib.request

SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "voie_libre": {"type": "boolean"},
        "conseil": {"type": "string",
                    "enum": ["avancer", "gauche", "droite", "demi_tour"]},
    },
    "required": ["scene", "voie_libre", "conseil"],
}

PROMPT = (
    "Tu es les yeux d'un petit robot chien qui patrouille dans une piece, a 20 cm "
    "du sol. Regarde cette image prise par sa camera frontale.\n"
    "Reponds en francais, en une seule phrase courte pour 'scene'.\n"
    "'voie_libre' est vrai SEULEMENT si le robot peut avancer d'un metre sans "
    "heurter un mur, un meuble, ni s'emmeler dans des cables au sol.\n"
    "'conseil' : avancer, gauche, droite ou demi_tour."
)


class Vision:
    """Analyse en arriere-plan. Le dernier resultat est lisible a tout moment."""

    def __init__(self, cfg, perception, journal=print):
        self.cfg = cfg["vision"]
        self.perception = perception
        self.journal = journal
        self.dernier = None          # dict ou None
        self.horodatage = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._verrou = threading.Lock()
        self.echecs = 0

    # -- appel au modele -----------------------------------------------------
    def _analyser(self, chemin):
        img = base64.b64encode(open(chemin, "rb").read()).decode()
        payload = {
            "model": self.cfg["modele"],
            "stream": False,
            "format": SCHEMA,
            "messages": [{"role": "user", "content": PROMPT, "images": [img]}],
            # budget genereux : le modele raisonne AVANT de repondre, et un budget
            # trop court le laisse coince dans `thinking` avec un `content` vide.
            "options": {"temperature": 0, "num_predict": 900},
        }
        req = urllib.request.Request(
            f"{self.cfg['hote'].rstrip('/')}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.cfg["timeout_s"]) as r:
            d = json.loads(r.read())
        contenu = (d.get("message") or {}).get("content", "").strip()
        if not contenu:
            raise ValueError(f"reponse vide (done_reason={d.get('done_reason')})")
        return json.loads(contenu)

    # -- boucle de fond ------------------------------------------------------
    def _boucle(self):
        while not self._stop.wait(self.cfg["intervalle_s"]):
            chemin = self.perception.photo(largeur=self.cfg["largeur_px"])
            if not chemin:
                self.journal("[vision] camera indisponible")
                continue
            t0 = time.time()
            try:
                r = self._analyser(chemin)
                r["latence_s"] = round(time.time() - t0, 1)
                r["photo"] = chemin
                with self._verrou:
                    self.dernier = r
                    self.horodatage = time.time()
                    self.echecs = 0
                self.journal(f"[vision] ({r['latence_s']}s) {r['scene']} "
                             f"— voie_libre={r['voie_libre']} conseil={r['conseil']}")
            except Exception as e:
                self.echecs += 1
                self.journal(f"[vision] echec {self.echecs} : {type(e).__name__}: {e}")

    def demarrer(self):
        if not self.cfg.get("active"):
            self.journal("[vision] desactivee — patrouille aux seuls capteurs")
            return
        self._thread = threading.Thread(target=self._boucle, daemon=True)
        self._thread.start()
        self.journal(f"[vision] active — {self.cfg['modele']} toutes les "
                     f"{self.cfg['intervalle_s']} s")

    def arreter(self):
        self._stop.set()

    # -- lecture -------------------------------------------------------------
    def avis(self, peremption_s=None):
        """Dernier avis, ou None s'il est trop vieux pour etre encore pertinent."""
        peremption_s = peremption_s or (self.cfg["intervalle_s"] * 2)
        with self._verrou:
            if not self.dernier or (time.time() - self.horodatage) > peremption_s:
                return None
            return dict(self.dernier)

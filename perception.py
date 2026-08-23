#!/usr/bin/env python3
"""
Perception de pidog-patrol : capteurs et camera.

Le point important de ce module est le FILTRAGE de l'ultrason. Le capteur est
monte sur la tete : chaque pas fait tanguer le corps, le faisceau part au plafond
et renvoie des echos parasites. Mesure face au meme mur (23/08/2026) :

    a l'arret   : 68 cm, ecart-type < 1 cm
    en marchant : 60 a 155 cm

Les parasites sont TOUJOURS plus longs que la vraie distance — un obstacle ne peut
pas etre plus loin qu'il n'est. On garde donc le MINIMUM de N mesures, jamais la
moyenne ni la mediane, qui laisseraient passer un « mur a 120 cm » inexistant.
"""
import os
import subprocess
import time


class Perception:
    def __init__(self, dog, cfg):
        self.dog = dog
        self.cfg = cfg
        self.o = cfg["obstacle"]

    # -- ultrason ------------------------------------------------------------
    def distance(self, n=None, portee_max=None):
        """Distance en cm, filtree du bruit de la marche. None si aucun echo."""
        n = n or self.o["mesures_par_controle"]
        portee_max = portee_max or self.o["portee_max_cm"]
        lues = []
        for _ in range(n):
            try:
                v = self.dog.read_distance()
                if 0 < v < portee_max:
                    lues.append(v)
            except Exception:
                pass
            time.sleep(0.03)
        return min(lues) if lues else None

    def balayage(self, angles=None):
        """Distances sur un arc de -90 a +90, en orientant la tete.

        INDISPENSABLE : l'ultrason est un cone etroit vers l'avant. Il ne voit
        RIEN sur les cotes — longer une porte en frottant est son angle mort exact.

        ⚠️ L'AMPLITUDE COMPTE AUTANT QUE LE PRINCIPE. Mesure du 23/08/2026, robot
        coince contre une porte :

            +90° = 42 cm   +45° = 10 cm   0° = 5 cm   -45° = 5 cm   -90° = 41 cm

        La seule issue reelle etait a -90°. A -45° le capteur lisait 5 cm et le
        robot se croyait bloque des deux cotes. Un balayage limite a +/-45°
        (mon premier choix) rate donc les sorties. Idee reprise de
        DrakeBG/PiDog-Left-Wall-Navigation, qui regarde a 90°.

        Convention : yaw POSITIF = gauche (cf. preset_actions.head_down_left).

        Retourne un dict avec, par cote :
            "<cote>_libre" : la plus GRANDE distance du cote (potentiel de fuite)
            "<cote>_pres"  : la plus PETITE distance du cote (risque de frottement)
        plus "centre", et "brut" pour le detail angle par angle.
        """
        angles = angles or self.o.get("balayage_angles_deg", [90, 45, 0, -45, -90])
        brut = {}
        for a in angles:
            self.dog.head_move([[a, 0, 0]], speed=90)
            self.dog.wait_all_done()
            time.sleep(0.12)                  # laisse l'echo se stabiliser
            brut[a] = self.distance()
        self.dog.head_move([[0, 0, 0]], speed=90)
        self.dog.wait_all_done()

        def agrege(cotes, plus_grand):
            vals = [brut[a] for a in cotes if a in brut]
            reels = [v for v in vals if v is not None]
            if not reels:
                return None            # aucun echo = espace libre
            return max(reels) if plus_grand else min(reels)

        gauche = [a for a in angles if a > 0]
        droite = [a for a in angles if a < 0]
        return {
            "centre": brut.get(0),
            "gauche_libre": agrege(gauche, True),
            "gauche_pres": agrege(gauche, False),
            "droite_libre": agrege(droite, True),
            "droite_pres": agrege(droite, False),
            "brut": brut,
        }

    def distance_confirmee(self, seuil, n=None, tolerance=None):
        """Distance frontale, avec vote de confirmation sous le seuil.

        Le filtre par le MINIMUM (voir distance()) protege des echos trop LONGS,
        qui sont le vrai risque en marchant. Mais il rend symetriquement vulnerable
        a un parasite trop COURT : une seule lecture aberrante a 8 cm suffirait a
        declencher un recul en pleine voie libre.

        On re-mesure donc toute lecture inferieure au seuil. Si les mesures ne
        s'accordent pas, on retient la PLUS GRANDE : un parasite court est bien
        plus frequent qu'un obstacle qui disparait entre deux mesures.

        Idee reprise de seven-lynx/HoundMind (vote de confirmation).
        """
        cfg = self.cfg.get("confirmation", {})
        d = self.distance()
        if not cfg.get("active", True) or d is None or d >= seuil:
            return d
        n = n or cfg.get("mesures", 2)
        tolerance = tolerance or cfg.get("tolerance_cm", 12)
        mesures = [d]
        for _ in range(max(0, n - 1)):
            time.sleep(0.05)
            v = self.distance()
            if v is not None:
                mesures.append(v)
        if len(mesures) < 2:
            return d
        if (max(mesures) - min(mesures)) <= tolerance:
            return min(mesures)          # accord : l'obstacle est reel
        return max(mesures)              # desaccord : on ne freine pas sur un parasite

    # -- IMU -----------------------------------------------------------------
    def inclinaison(self):
        """(pitch, roll) en degres. Sert a detecter une chute ou un soulevement.

        Attention : `dog.rpy` reste a [0,0,0] sur cette version de la lib, c'est un
        buffer de consigne. Seuls .pitch et .roll sont reellement mis a jour.
        """
        try:
            return float(self.dog.pitch), float(self.dog.roll)
        except Exception:
            return 0.0, 0.0

    def bascule(self):
        """True si le robot est trop incline : tombe, ou souleve."""
        seuil = self.cfg["securite"]["inclinaison_max_deg"]
        p, r = self.inclinaison()
        return abs(p) > seuil or abs(r) > seuil

    # -- tactile -------------------------------------------------------------
    def touche(self):
        if not self.cfg["securite"]["arret_tactile"]:
            return False
        try:
            return self.dog.dual_touch.read() != 'N'
        except Exception:
            return False

    # -- batterie ------------------------------------------------------------
    def tension(self, n=5):
        """Mediane de n lectures. Ici la mediane suffit : pas de biais directionnel,
        contrairement a l'ultrason."""
        import statistics
        from robot_hat.device import get_battery_voltage
        lues = []
        for _ in range(n):
            try:
                lues.append(get_battery_voltage())
            except Exception:
                pass
            time.sleep(0.03)
        return statistics.median(lues) if lues else None

    # -- camera --------------------------------------------------------------
    def photo(self, chemin="/tmp/pidog_patrol.jpg", largeur=640):
        """Capture une image. Retourne le chemin, ou None si la camera est absente.

        rpicam-still est lance en sous-processus plutot que via picamera2 : le
        module python garde la camera ouverte et entre en conflit avec le reste.
        """
        hauteur = int(largeur * 3 / 4)
        try:
            r = subprocess.run(
                ["rpicam-still", "-o", chemin, "--width", str(largeur),
                 "--height", str(hauteur), "-t", "800", "-n"],
                capture_output=True, timeout=25,
                env={**os.environ, "PATH": os.environ.get("PATH", "") + ":/usr/bin:/usr/sbin"})
            return chemin if r.returncode == 0 and os.path.exists(chemin) else None
        except Exception:
            return None

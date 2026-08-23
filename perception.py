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

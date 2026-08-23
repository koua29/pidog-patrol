#!/usr/bin/env python3
"""
pidog-patrol — mode patrouille fiable pour le robot chien SunFounder PiDog.

Trois couches, du plus rapide au plus lent :

  1. SECURITE (a chaque cycle, ~200 ms)   tactile, inclinaison, batterie, duree
  2. REFLEXE  (a chaque cycle)            ultrason filtre -> avancer ou tourner
  3. VISION   (thread, ~45 s)             decrit la scene, rend prudent

La navigation ne depend JAMAIS de la couche 3 : qwen3-vl met ~26 s par image
(mesure), ce qui est inutilisable pour piloter des pattes. La vision ne peut que
rendre le robot plus prudent, jamais l'autoriser a passer.

    python3 patrol.py                 patrouille (duree_max_s de la config)
    python3 patrol.py --duree 120     patrouille 2 minutes
    python3 patrol.py --sans-vision   capteurs seuls
    python3 patrol.py --verifier      valide la config et l'environnement
    python3 patrol.py --capteurs      affiche les capteurs en continu, sans bouger
    python3 patrol.py --simulation    deroule toute la logique SANS bouger les servos
"""
import json
import os
import signal
import subprocess
import sys
import time

RACINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RACINE)
CONFIG = os.environ.get("PIDOG_PATROL_CONFIG", os.path.join(RACINE, "config.json"))

MARCHE_AUTORISEE = os.environ.get("PIDOG_MARCHE", "0") == "1"


_ARRET_DEMANDE = {"oui": False}


def _sur_signal(signum, _frame):
    """Pidog() lance des threads non-daemon : sans ce gestionnaire, un SIGTERM
    (timeout, systemd, pkill) laisse le processus vivant et le GPIO verrouille,
    ce qui fait echouer silencieusement TOUS les lancements suivants."""
    _ARRET_DEMANDE["oui"] = True
    print(f"\n-- signal {signum} recu, arret propre demande", flush=True)


signal.signal(signal.SIGTERM, _sur_signal)
signal.signal(signal.SIGINT, _sur_signal)


def journal(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# --------------------------------------------------------------------- config
def charger_config(chemin=CONFIG):
    with open(chemin, encoding="utf-8") as f:
        c = json.load(f)
    o, s = c["obstacle"], c["securite"]
    if o["seuil_cm"] <= 0 or o["seuil_cm"] > o["portee_max_cm"]:
        raise ValueError("obstacle.seuil_cm incoherent")
    if o["rotation_pas_max"] < o["rotation_pas"]:
        raise ValueError("rotation_pas_max doit etre >= rotation_pas")
    if not 0 < s["inclinaison_max_deg"] <= 90:
        raise ValueError("inclinaison_max_deg doit etre entre 0 et 90")
    if s["duree_max_s"] <= 0:
        raise ValueError("duree_max_s doit etre > 0")
    return c


# ------------------------------------------------------------------------ voix
def parler(texte, cfg):
    if not texte:
        return
    journal(f"   PiDog> {texte}")
    wav = "/tmp/pidog_patrol_tts.wav"
    try:
        subprocess.run(["pico2wave", "-l", cfg["voix"]["langue"], "-w", wav, texte],
                       check=True, capture_output=True, timeout=15)
        subprocess.run(["aplay", "-q", "-D", "plug:speaker", wav],
                       timeout=30, capture_output=True)
    except Exception as e:
        journal(f"   (voix indisponible : {type(e).__name__})")


def preparer_audio():
    """PipeWire elit parfois la sortie HDMI : le robot parlerait dans le vide."""
    import re

    def pactl(*a):
        return subprocess.run(["pactl", *a], capture_output=True,
                              text=True, timeout=5).stdout
    try:
        sinks = pactl("list", "sinks", "short")
        hp = next((l.split("\t")[1] for l in sinks.splitlines() if "soc_sound" in l), None)
        if hp and pactl("get-default-sink").strip() != hp:
            pactl("set-default-sink", hp)
        v = pactl("get-sink-volume", "@DEFAULT_SINK@")
        m = re.search(r"(\d+)%", v)
        if m and int(m.group(1)) < 90:
            pactl("set-sink-volume", "@DEFAULT_SINK@", "100%")
        pactl("set-sink-mute", "@DEFAULT_SINK@", "0")
    except Exception:
        pass


# ------------------------------------------------- cohabitation avec pidog-voice
def ecoute_vocale_active():
    return subprocess.run(["pgrep", "-f", "[p]idog_ears"],
                          capture_output=True).returncode == 0


def suspendre_ecoute_vocale():
    """Deux objets Pidog() dans deux processus se disputent le GPIO ('GPIO busy').
    La patrouille prend donc la main, et rend l'ecoute a la fin."""
    if not ecoute_vocale_active():
        return False
    journal("[voice] ecoute vocale suspendue (conflit GPIO inevitable)")
    subprocess.run(["pkill", "-f", "[e]ars_loop"], capture_output=True)
    subprocess.run(["pkill", "-f", "[p]idog_ears"], capture_output=True)
    for _ in range(20):
        if not ecoute_vocale_active():
            break
        time.sleep(0.5)
    return True


def relancer_ecoute_vocale():
    sup = os.path.expanduser("~/script/pidog-voice/pi/ears_loop.sh")
    if not os.path.exists(sup):
        journal("[voice] superviseur introuvable, ecoute non relancee")
        return
    journal("[voice] ecoute vocale relancee")
    subprocess.Popen(["setsid", "nohup", sup],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)


# ------------------------------------------------------------------ patrouille
class Patrouille:
    def __init__(self, dog, cfg, perception, vision, simulation=False):
        self.simulation = simulation
        self.dog = dog
        self.cfg = cfg
        self.p = perception
        self.vision = vision
        self.blocages = 0
        self.distances = []          # distances des cycles ou l'on a AVANCE
        self.sans_echo = 0
        self.raison_arret = None

    # -- securite ------------------------------------------------------------
    def danger(self, debut):
        s = self.cfg["securite"]
        if self.p.touche():
            self.raison_arret = "tactile"
            return True
        if self.p.bascule():
            p, r = self.p.inclinaison()
            self.raison_arret = f"inclinaison ({p:.0f}°/{r:.0f}°)"
            return True
        if time.time() - debut > s["duree_max_s"]:
            self.raison_arret = "duree maximale"
            return True
        return False

    def batterie_suffisante(self):
        v = self.p.tension()
        seuil = self.cfg["securite"]["batterie_min_v"]
        if v is not None and v < seuil:
            self.raison_arret = f"batterie {v:.2f} V < {seuil} V"
            return False
        return True

    # -- decision ------------------------------------------------------------
    def coince(self):
        """Coince = j'AVANCE mais la distance ne diminue pas.

        Avancer vers un obstacle doit le rapprocher. Si la distance reste figee
        alors qu'on avance, c'est qu'on pousse contre quelque chose que l'ultrason
        ne voit pas (pied de chaise, tapis, angle). On ne compare donc QUE les
        cycles ou l'on a effectivement avance : sinon une serie de rotations, qui
        laisse naturellement la distance stable, serait prise pour un blocage.
        """
        b = self.cfg["blocage"]
        n = b["cycles_avant_demi_tour"]
        if len(self.distances) < n:
            return False
        recentes = self.distances[-n:]
        return (max(recentes) - min(recentes)) < b["tolerance_cm"]

    def decider(self, dist):
        """Retourne ('avancer'|'gauche'|'droite'|'demi_tour', raison)."""
        o = self.cfg["obstacle"]
        b = self.cfg["blocage"]

        if dist is None:
            # Aucun echo. Le plus souvent : rien dans la portee du capteur, donc
            # voie DEGAGEE — surtout pas un demi-tour. Mais un mur absorbant ou un
            # capteur qui lache donnent le meme silence : au bout de plusieurs
            # cycles sans le moindre echo, on prefere tourner par prudence.
            self.sans_echo += 1
            if self.sans_echo >= b.get("cycles_sans_echo", 6):
                return "gauche", f"aucun echo depuis {self.sans_echo} cycles"
            return "avancer", "aucun echo (voie probablement degagee)"
        self.sans_echo = 0

        if dist < o["seuil_cm"]:
            return "gauche", f"obstacle a {dist:.0f} cm"

        if self.coince():
            return "demi_tour", "j'avance sans me rapprocher : coince"

        # la vision ne peut que RENDRE PRUDENT, jamais autoriser un passage
        if self.cfg["vision"].get("influence_navigation"):
            avis = self.vision.avis() if self.vision else None
            if avis and not avis.get("voie_libre"):
                conseil = avis.get("conseil", "gauche")
                if conseil in ("gauche", "droite", "demi_tour"):
                    return conseil, f"vision : {avis['scene'][:60]}"

        return "avancer", f"voie libre a {dist:.0f} cm"

    # -- execution -----------------------------------------------------------
    def agir(self, action):
        o = self.cfg["obstacle"]
        d = self.dog
        if self.simulation:
            # valide la logique de decision sans bouger : aucun risque de chute
            time.sleep(0.8)
            return
        if action == "avancer":
            self.blocages = 0
            d.rgb_strip.set_mode('breath', color='green', bps=1)
            d.do_action('forward', step_count=o["pas_par_cycle"], speed=90)
        else:
            self.blocages += 1
            pas = min(o["rotation_pas"] + self.blocages - 1, o["rotation_pas_max"])
            d.rgb_strip.set_mode('bark', color='red', bps=2)
            d.speak('single_bark_1')
            if action == "demi_tour":
                pas = o["rotation_pas_max"]
            sens = 'turn_right' if action == "droite" else 'turn_left'
            d.do_action(sens, step_count=pas, speed=88)
        d.wait_all_done()

    # -- boucle --------------------------------------------------------------
    def executer(self, duree_max=None):
        cfg = self.cfg
        if duree_max:
            cfg["securite"]["duree_max_s"] = duree_max

        if not self.batterie_suffisante():
            parler(cfg["voix"]["batterie_basse"], cfg)
            journal(f"!! {self.raison_arret}")
            return 1

        d = self.dog
        if not self.simulation:
            d.do_action('stand', speed=70)
            d.wait_all_done()
            d.head_move([[0, 0, 0]], speed=80)   # tete droite : faisceau vers l'avant
            d.wait_all_done()
            parler(cfg["voix"]["depart"], cfg)
        else:
            journal("== SIMULATION : la logique tourne, les servos ne bougent pas ==")

        debut = time.time()
        cycle = 0
        while True:
            if _ARRET_DEMANDE["oui"]:
                self.raison_arret = "signal d'arret"
                break
            if self.danger(debut):
                break
            cycle += 1
            dist = self.p.distance()
            action, raison = self.decider(dist)
            if action == "avancer" and dist is not None:
                self.distances.append(dist)   # seuls les cycles d'avance comptent
            else:
                self.distances.clear()        # une rotation remet le compteur a zero
            journal(f"[{cycle:03d}] {dist if dist is None else f'{dist:.0f} cm':>7} "
                    f"-> {action:<10} ({raison})")
            self.agir(action)
            if cycle % 10 == 0 and not self.batterie_suffisante():
                break

        if not self.simulation:
            d.legs_stop()
        d.rgb_strip.set_mode('breath', color='white', bps=0.5)
        urgence = self.raison_arret in ("tactile",) or "inclinaison" in (self.raison_arret or "")
        journal(f"== arret : {self.raison_arret} — {cycle} cycles en "
                f"{time.time()-debut:.0f} s")
        if not self.simulation:
            parler(cfg["voix"]["arret_urgence"] if urgence else cfg["voix"]["fin"], cfg)
            d.do_action('sit', speed=60)
            d.wait_all_done()
        return 0


# ------------------------------------------------------------------------ main
def mode_capteurs(cfg):
    """Affiche les capteurs sans bouger : sert a regler les seuils.

    Suspend l'ecoute vocale comme la patrouille : sans cela, l'ultrason et le
    tactile echouent silencieusement a l'init (GPIO deja pris) et renvoient -1.
    """
    from pidog import Pidog
    from perception import Perception
    reprendre = suspendre_ecoute_vocale()
    dog = Pidog()
    try:
        time.sleep(1.5)
        p = Perception(dog, cfg)
        journal("capteurs (Ctrl-C pour sortir) — le robot ne bouge pas")
        while not _ARRET_DEMANDE["oui"]:
            dist = p.distance()
            pi, ro = p.inclinaison()
            journal(f"  distance={dist if dist is None else f'{dist:6.1f} cm'}  "
                    f"pitch={pi:6.1f}°  roll={ro:6.1f}°  "
                    f"tactile={'OUI' if p.touche() else 'non'}  "
                    f"batterie={p.tension():.2f} V")
            time.sleep(1)
    except KeyboardInterrupt:
        journal("fin")
    finally:
        dog.close()
        if reprendre:
            relancer_ecoute_vocale()
    return 0


def main():
    args = sys.argv[1:]
    try:
        cfg = charger_config()
    except Exception as e:
        journal(f"!! config invalide : {e}")
        return 1

    if "--sans-vision" in args:
        cfg["vision"]["active"] = False

    if "--verifier" in args:
        journal(f"config valide — seuil {cfg['obstacle']['seuil_cm']} cm, "
                f"duree max {cfg['securite']['duree_max_s']} s, "
                f"vision {'active' if cfg['vision']['active'] else 'desactivee'}")
        journal(f"marche {'AUTORISEE' if MARCHE_AUTORISEE else 'INTERDITE (PIDOG_MARCHE=1 requis)'}")
        journal(f"ecoute vocale {'active — elle sera suspendue' if ecoute_vocale_active() else 'inactive'}")
        return 0

    if "--capteurs" in args:
        return mode_capteurs(cfg)

    simulation = "--simulation" in args
    if not MARCHE_AUTORISEE and not simulation:
        journal("!! REFUS : la patrouille deplace le robot.")
        journal("   S'il est au SOL et non sur une table : PIDOG_MARCHE=1 python3 patrol.py")
        journal("   Pour valider la logique sans bouger : python3 patrol.py --simulation")
        return 2

    duree = None
    if "--duree" in args:
        duree = int(args[args.index("--duree") + 1])

    preparer_audio()
    reprendre_ecoute = suspendre_ecoute_vocale()

    from pidog import Pidog
    from perception import Perception
    from vision import Vision

    dog = Pidog()
    vision = None
    try:
        time.sleep(1.5)
        p = Perception(dog, cfg)
        if p.distance() is None and p.distance() is None:
            journal("!! ULTRASON MUET a l'initialisation.")
            journal("   Cause la plus frequente : un autre programme detient le GPIO")
            journal("   (pidog-voice). Verifier : pgrep -af pidog_ears")
            return 3
        vision = Vision(cfg, p, journal)
        vision.demarrer()
        code = Patrouille(dog, cfg, p, vision, simulation).executer(duree)
    except KeyboardInterrupt:
        journal("interrompu au clavier")
        code = 0
    finally:
        if vision:
            vision.arreter()
        dog.close()
        if reprendre_ecoute:
            relancer_ecoute_vocale()
    return code


if __name__ == "__main__":
    sys.exit(main())

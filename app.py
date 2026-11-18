from concurrent.futures import ThreadPoolExecutor
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import streamlit as st
from pypdf import PdfReader
from google import genai
from groq import Groq
from gtts import gTTS

st.set_page_config(page_title="Révision IA", page_icon="🎧")
st.title("🎧 Mon Appli de Révision IA")


def lire_secret(nom: str) -> str:
    """Lit une clé secrète côté serveur uniquement (jamais envoyée au navigateur)."""
    try:
        return st.secrets[nom]
    except Exception:
        return ""


MODELES_GEMINI = [
    "gemini-3.8-flash-lite",
    "gemini-3.8-flash",
    "gemini-flash-lite-latest",
]

VOIX_FRANCAISES = {
    "Vivienne (Femme — très fluide, style podcast)": "fr-FR-VivienneMultilingualNeural",
    "Rémy (Homme — naturel et posé)": "fr-FR-RemyMultilingualNeural",
    "Denise (Femme — dynamique)": "fr-FR-DeniseNeural",
    "Henri (Homme — classique)": "fr-FR-HenriNeural",
}

VOIX_PROF_DUO = "fr-FR-RemyMultilingualNeural"
VOIX_ELEVE_DUO = "fr-FR-VivienneMultilingualNeural"


def nettoyer_cle(cle: str) -> str:
    return cle.strip().encode("ascii", "ignore").decode("ascii")


def nettoyer_texte_pour_oral(texte: str) -> str:
    texte = re.sub(r"[*_#`]", "", texte)
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip()


def _generer_segment_edge_tts(texte: str, voix: str, vitesse: str) -> bytes:
    """Génère un segment MP3 via Edge TTS dans un processus isolé (timeout 15s)."""
    texte_propre = nettoyer_texte_pour_oral(texte)
    tmp_txt = None
    tmp_mp3 = None

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f_txt:
            f_txt.write(texte_propre)
            tmp_txt = f_txt.name

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_mp3:
            tmp_mp3 = f_mp3.name

        resultat = subprocess.run(
            [
                sys.executable, "-m", "edge_tts",
                "--voice", voix,
                f"--rate={vitesse}",
                "--file", tmp_txt,
                "--write-media", tmp_mp3,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if resultat.returncode == 0 and os.path.exists(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
            with open(tmp_mp3, "rb") as f:
                return f.read()

        raise RuntimeError((resultat.stderr or resultat.stdout or "réponse vide").strip())
    finally:
        for chemin in (tmp_txt, tmp_mp3):
            if chemin and os.path.exists(chemin):
                try:
                    os.remove(chemin)
                except OSError:
                    pass


def texte_vers_mp3(texte: str, voix: str, vitesse: str = "-12%") -> tuple[bytes, str]:
    """Convertit un texte en MP3 fluide via Edge TTS (secours automatique gTTS)."""
    try:
        return _generer_segment_edge_tts(texte, voix, vitesse), "Voix fluide (Edge TTS)"
    except Exception as e:
        tts = gTTS(text=nettoyer_texte_pour_oral(texte), lang="fr")
        mp3_buffer = io.BytesIO()
        tts.write_to_fp(mp3_buffer)
        mp3_buffer.seek(0)
        return mp3_buffer.read(), f"Secours gTTS ({str(e)[:80]})"


def generer_dialogue_mp3(script_brut: str, vitesse: str, barre_progression=None) -> tuple[bytes, str]:
    """Assemble un podcast MP3 à 2 voix (Rémy = Prof, Vivienne = Étudiante)."""
    lignes_dialogue = []
    for ligne in script_brut.splitlines():
        ligne = ligne.strip()
        match = re.match(r"^(PROF|ELEVE|ÉLÈVE)\s*:\s*(.+)$", ligne, re.IGNORECASE)
        if match:
            role = "PROF" if match.group(1).upper() == "PROF" else "ELEVE"
            replique = nettoyer_texte_pour_oral(match.group(2))
            if replique:
                lignes_dialogue.append((role, replique))

    if len(lignes_dialogue) < 2:
        return texte_vers_mp3(script_brut, VOIX_PROF_DUO, vitesse)

    audio_complet = bytearray()
    total = len(lignes_dialogue)
    try:
        for i, (role, replique) in enumerate(lignes_dialogue):
            voix = VOIX_PROF_DUO if role == "PROF" else VOIX_ELEVE_DUO
            segment = _generer_segment_edge_tts(replique, voix, vitesse)
            audio_complet.extend(segment)
            if barre_progression is not None:
                barre_progression.progress((i + 1) / total)
        return bytes(audio_complet), "Dialogue 2 voix (Rémy & Vivienne)"
    except Exception:
        texte_complet = " ".join(rep for _, rep in lignes_dialogue)
        return texte_vers_mp3(texte_complet, VOIX_PROF_DUO, vitesse)


def generer_texte_ia(prompt: str, gemini_key: str, groq_key: str) -> tuple[str, str]:
    """Essaie Gemini si renseigné, sinon utilise Groq avec timeout strict."""
    erreurs = []

    if gemini_key:
        client_gemini = genai.Client(api_key=gemini_key)
        for modele in MODELES_GEMINI:
            try:
                def requete_gemini():
                    return client_gemini.models.generate_content(model=modele, contents=prompt)

                with ThreadPoolExecutor(max_workers=1) as executor:
                    reponse = executor.submit(requete_gemini).result(timeout=10)
                return reponse.text, f"Gemini ({modele})"
            except Exception as e:
                erreurs.append(f"Gemini {modele}: {e}")
                continue

    if groq_key:
        try:
            client_groq = Groq(api_key=groq_key, timeout=18.0)
            modeles_groq = [
                m.id for m in client_groq.models.list().data
                if getattr(m, "active", True)
                and not any(mot in m.id.lower() for mot in ["whisper", "guard", "tts", "playai", "orpheus"])
            ]
            modeles_groq.sort(
                key=lambda x: 0 if any(k in x.lower() for k in ["70b", "llama-4", "qwen", "versatile"]) else 1
            )

            for modele_groq in modeles_groq[:3]:
                try:
                    chat = client_groq.chat.completions.create(
                        model=modele_groq,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.5,
                    )
                    return chat.choices[0].message.content, f"Groq ({modele_groq})"
                except Exception as e:
                    erreurs.append(f"Groq {modele_groq}: {e}")
                    continue
        except Exception as e:
            erreurs.append(f"Groq: {e}")

    raise RuntimeError("Impossible de joindre l'IA.\n" + "\n".join(erreurs))


def transcrire_audio_groq(audio_bytes: bytes, groq_key: str) -> str:
    """Transcrit l'enregistrement micro en texte français via Whisper sur Groq."""
    client_groq = Groq(api_key=groq_key, timeout=15.0)
    modeles_whisper = [
        m.id for m in client_groq.models.list().data
        if getattr(m, "active", True) and "whisper" in m.id.lower()
    ]
    if not modeles_whisper:
        raise RuntimeError("Aucun modèle Whisper actif trouvé sur ton compte Groq.")

    modeles_whisper.sort(key=lambda x: 0 if "turbo" in x.lower() else 1)
    transcription = client_groq.audio.transcriptions.create(
        file=("reponse.wav", audio_bytes),
        model=modeles_whisper[0],
        language="fr",
    )
    return transcription.text


def generer_etape_guidee(
    texte_cours: str,
    plan: list[str],
    index_etape: int,
    gemini_key: str,
    groq_key: str,
    voix_id: str,
    vitesse_voix: str,
    plus_simple: bool = False,
):
    """Génère une explication orale courte + une micro-question pour l'étape en cours."""
    etape_actuelle = plan[index_etape]
    numero = index_etape + 1
    total = len(plan)

    if plus_simple:
        consigne = (
            f"L'étudiant n'a pas bien saisi l'étape {numero}/{total} : « {etape_actuelle} ».\n"
            f"Réexplique cette même notion BEAUCOUP plus simplement, comme à un ami qui fait son footing sans feuille sous les yeux.\n"
            f"Utilise une image ou une analogie concrète de la vie de tous les jours, en 70 mots maximum, avec des phrases très courtes.\n"
            f"Termine par UNE micro-question ultra simple pour vérifier que l'image est claire."
        )
    else:
        consigne = (
            f"Tu es un professeur patient qui fait un cours oral interactif à un étudiant qui court (sans feuille ni schéma).\n"
            f"Nous sommes à l'étape {numero} sur {total} du plan : « {etape_actuelle} ».\n"
            f"Explique UNIQUEMENT cette petite brique en 90 mots maximum (environ 40 secondes à l'oral).\n"
            f"Règles strictes :\n"
            f"- Fais des phrases très courtes et respirées.\n"
            f"- Utilise une analogie mentale simple au lieu de lister des détails techniques.\n"
            f"- Zéro liste à puces, zéro symbole mathématique brut.\n"
            f"- Termine OBLIGATOIREMENT par UNE micro-question courte qui demande une réponse rapide (5 à 10 secondes) pour vérifier la compréhension."
        )

    prompt = f"{consigne}\n\nCours de référence :\n{texte_cours[:12000]}"
    explication, _ = generer_texte_ia(prompt, gemini_key, groq_key)
    audio_etape, _ = texte_vers_mp3(explication, voix_id, vitesse_voix)

    st.session_state["guide_tour_id"] += 1
    st.session_state["guide_texte"] = explication.strip()
    st.session_state["guide_audio"] = audio_etape
    st.session_state.pop("guide_feedback_texte", None)
    st.session_state.pop("guide_feedback_audio", None)
    st.session_state.pop("guide_transcription", None)


def reinitialiser_cours():
    cles_a_vider = [
        "podcast_audio", "podcast_script", "podcast_info",
        "plan_cours", "etape_index", "guide_texte", "guide_audio",
        "guide_feedback_texte", "guide_feedback_audio", "guide_transcription",
        "question_actuelle", "question_audio", "feedback_texte", "feedback_audio",
        "transcription_user",
    ]
    for cle in cles_a_vider:
        st.session_state.pop(cle, None)
    st.session_state["historique_questions"] = []
    st.session_state["a_revoir"] = []
    st.session_state["est_rappel"] = False


# Initialisation de la mémoire de session
for cle, valeur in [
    ("q_id", 0),
    ("guide_tour_id", 0),
    ("historique_questions", []),
    ("a_revoir", []),
    ("est_rappel", False),
]:
    if cle not in st.session_state:
        st.session_state[cle] = valeur

# ==========================================
# BARRE LATÉRALE : VOIX, RYTHME & MÉMOIRE
# ==========================================
with st.sidebar:
    st.header("🎙️ Confort d'écoute")
    vitesse_voix = st.select_slider(
        "Rythme de la voix :",
        options=["-25%", "-18%", "-12%", "-5%", "+0%", "+10%"],
        value="-12%",
        help="Recommandé : -12% ou -18% pour découvrir un cours sans feuille."
    )
    choix_voix = st.selectbox("Voix solo (Cours & Quiz) :", list(VOIX_FRANCAISES.keys()))
    voix_id = VOIX_FRANCAISES[choix_voix]

    # Lecture des clés uniquement côté serveur (invisible dans l'interface et dans le code navigateur)
    groq_key = nettoyer_cle(lire_secret("GROQ_API_KEY"))
    gemini_key = nettoyer_cle(lire_secret("GEMINI_API_KEY"))

    # Champ affiché uniquement en développement local si aucun secret serveur n'est configuré
    if not groq_key and not gemini_key:
        with st.expander("🔑 Configuration locale"):
            groq_key = nettoyer_cle(st.text_input("Clé API Groq :", type="password"))
            gemini_key = nettoyer_cle(st.text_input("Clé API Gemini (optionnelle) :", type="password"))

    if st.session_state["a_revoir"]:
        st.divider()
        st.subheader(f"🧠 File mémoire ({len(st.session_state['a_revoir'])})")
        maintenant = time.time()
        for i, item in enumerate(st.session_state["a_revoir"], start=1):
            restant = int(item["heure_rappel"] - maintenant)
            if restant <= 0:
                st.write(f"**{i}.** Prête à être reposée !")
            else:
                minutes, secondes = divmod(restant, 60)
                st.write(f"**{i}.** Rappel dans {minutes} min {secondes:02d} s")

        if st.button("⏩ Simuler +10 min (tester le rappel)"):
            for item in st.session_state["a_revoir"]:
                item["heure_rappel"] = time.time() - 1
            st.rerun()

# ==========================================
# UPLOAD DU COURS
# ==========================================
uploaded_file = st.file_uploader(
    "Dépose ton cours (PDF ou fichier texte)",
    type=["pdf", "txt"]
)

if uploaded_file is not None:
    if st.session_state.get("fichier_actuel") != uploaded_file.name:
        reinitialiser_cours()
        st.session_state["fichier_actuel"] = uploaded_file.name

    if uploaded_file.name.endswith(".pdf"):
        reader = PdfReader(uploaded_file)
        texte_cours = "\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        texte_cours = uploaded_file.read().decode("utf-8", errors="ignore")

    if not texte_cours.strip():
        st.warning("Aucun texte détecté. Essaie avec un PDF contenant du vrai texte.")
    else:
        st.success(f"Cours chargé : **{uploaded_file.name}** ({len(texte_cours)} caractères)")

        tab_podcast, tab_guide, tab_quiz = st.tabs([
            "🎙️ 1. Podcast Découverte",
            "🧭 2. Cours Guidé (Pas-à-pas)",
            "🔥 3. Crash-test & Mémoire",
        ])

        # ==========================================
        # MODE 1 : PODCAST DÉCOUVERTE (2 VOIX OU SOLO)
        # ==========================================
        with tab_podcast:
            st.caption("Idéal pour découvrir le cours en courant : vue d'ensemble d'abord, zéro surcharge d'infos.")
            col1, col2 = st.columns(2)
            with col1:
                format_audio = st.radio(
                    "Format audio :",
                    ["Dialogue à 2 voix (Rémy & Vivienne)", "Monologue calme (1 voix)"],
                )
            with col2:
                profondeur = st.radio(
                    "Niveau de détail :",
                    ["Vue d'ensemble (Découverte sans jargon)", "Approfondi (Mécanismes & exemples)"],
                )

            if st.button("🎧 Générer le podcast MP3"):
                if not gemini_key and not groq_key:
                    st.warning("Aucune clé API configurée côté serveur.")
                else:
                    with st.status("Fabrication de ton podcast...", expanded=True) as status:
                        try:
                            est_duo = "2 voix" in format_audio
                            est_decouverte = "Vue d'ensemble" in profondeur

                            consigne_fond = (
                                "Reste très accessible : vulgarise, donne d'abord une vue d'ensemble claire en 30 secondes "
                                "(de quoi ça parle, à quoi ça sert, les 3 grandes idées), utilise des images concrètes, "
                                "et évite tout jargon inutile."
                                if est_decouverte
                                else "Commence par une vue d'ensemble en 30 secondes pour poser la carte mentale, "
                                     "puis explique progressivement les mécanismes clés et leurs liens logiques avec des analogies claires."
                            )

                            if est_duo:
                                nb_echanges = "8 à 10 répliques courtes" if est_decouverte else "14 à 16 répliques"
                                prompt = (
                                    f"Écris un script de podcast français à 2 voix destiné à un étudiant qui court sans feuille sous les yeux.\n"
                                    f"Personnages :\n"
                                    f"- PROF : un enseignant calme et pédagogue.\n"
                                    f"- ELEVE : une étudiante curieuse qui reformule, pose des questions simples ('Attends, concrètement ça veut dire quoi ?') et fait avancer la discussion.\n\n"
                                    f"Objectif pédagogique : {consigne_fond}\n"
                                    f"Longueur : {nb_echanges} (2 à 3 phrases courtes max par réplique).\n"
                                    f"FORMAT STRICT OBLIGATOIRE : chaque ligne doit commencer exactement par 'PROF:' ou 'ELEVE:' suivi du texte parlé. "
                                    f"Aucune didascalie, aucun astérisque, aucun symbole mathématique brut.\n\n"
                                    f"Cours :\n{texte_cours[:12000]}"
                                )
                            else:
                                longueur = "environ 180 mots" if est_decouverte else "environ 350 mots"
                                prompt = (
                                    f"Tu es un animateur de podcast pédagogique qui parle calmement en français à un étudiant qui court sans schéma.\n"
                                    f"Objectif : {consigne_fond}\n"
                                    f"Longueur : {longueur}, avec des phrases très courtes et des transitions douces.\n"
                                    f"Règles strictes : uniquement du texte à lire à voix haute, sans puces, sans titres, sans astérisques ni symboles mathématiques bruts.\n\n"
                                    f"Cours :\n{texte_cours[:12000]}"
                                )

                            st.write("1/2 — Écriture du script adapté à l'écoute...")
                            script_audio, moteur_utilise = generer_texte_ia(prompt, gemini_key, groq_key)

                            st.write("2/2 — Enregistrement vocal en cours...")
                            if est_duo:
                                barre = st.progress(0.0)
                                audio_bytes, info_voix = generer_dialogue_mp3(script_audio, vitesse_voix, barre)
                            else:
                                audio_bytes, info_voix = texte_vers_mp3(script_audio, voix_id, vitesse_voix)

                            st.session_state["podcast_audio"] = audio_bytes
                            st.session_state["podcast_script"] = script_audio
                            st.session_state["podcast_info"] = f"{moteur_utilise} • {info_voix}"
                            status.update(label="Podcast prêt !", state="complete", expanded=False)
                        except Exception as e:
                            status.update(label="Erreur", state="error")
                            st.error(str(e))

            if "podcast_audio" in st.session_state:
                st.success(f"Podcast prêt ! ({st.session_state['podcast_info']})")
                st.audio(st.session_state["podcast_audio"], format="audio/mp3")
                st.download_button(
                    label="⬇️ Télécharger le podcast (.mp3)",
                    data=st.session_state["podcast_audio"],
                    file_name="podcast_revision.mp3",
                    mime="audio/mp3"
                )
                with st.expander("Lire le script du podcast"):
                    st.write(st.session_state["podcast_script"])

        # ==========================================
        # MODE 2 : COURS GUIDÉ PAS-À-PAS (INTERACTIF)
        # ==========================================
        with tab_guide:
            st.caption(
                "L'IA découpe le cours en petites briques digestes : vue d'ensemble d'abord, puis une idée à la fois avec une micro-question. "
                "Au micro, tu peux répondre ou simplement dire « plus simple » ou « suivant »."
            )

            if "plan_cours" not in st.session_state:
                if st.button("🚀 Démarrer le cours guidé pas-à-pas"):
                    if not groq_key:
                        st.warning("Aucune clé API Groq configurée côté serveur.")
                    else:
                        with st.status("Découpage intelligent du cours...", expanded=True) as status:
                            try:
                                st.write("1/2 — Création du plan progressif (vue d'ensemble -> détails)...")
                                prompt_plan = (
                                    f"Découpe ce cours en 4 ou 5 mini-étapes progressives pour l'enseigner à l'oral à quelqu'un qui court.\n"
                                    f"L'étape 1 doit OBLIGATOIREMENT être la vue d'ensemble (de quoi parle le cours et à quoi ça sert).\n"
                                    f"Les étapes suivantes découpent les concepts clés un par un, du plus simple au plus avancé.\n"
                                    f"FORMAT STRICT : écris uniquement 4 ou 5 lignes numérotées comme ceci :\n"
                                    f"1. Vue d'ensemble : ...\n"
                                    f"2. ...\n"
                                    f"Aucun texte avant ou après.\n\n"
                                    f"Cours :\n{texte_cours[:12000]}"
                                )
                                plan_brut, _ = generer_texte_ia(prompt_plan, gemini_key, groq_key)
                                etapes = []
                                for ligne in plan_brut.splitlines():
                                    propre = re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", ligne).strip()
                                    if len(propre) > 5:
                                        etapes.append(propre)
                                if not etapes:
                                    etapes = ["Vue d'ensemble du cours", "Concept principal", "Application et synthèse"]

                                st.session_state["plan_cours"] = etapes[:5]
                                st.session_state["etape_index"] = 0

                                st.write("2/2 — Préparation de l'étape 1 (Vue d'ensemble)...")
                                generer_etape_guidee(
                                    texte_cours, st.session_state["plan_cours"], 0,
                                    gemini_key, groq_key, voix_id, vitesse_voix
                                )
                                status.update(label="Cours guidé prêt !", state="complete", expanded=False)
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))
            else:
                plan = st.session_state["plan_cours"]
                idx = st.session_state["etape_index"]

                if idx >= len(plan):
                    st.success("🎉 Bravo ! Tu as parcouru toutes les étapes de ce cours. Tu peux maintenant tester ta mémoire dans l'onglet 3 !")
                    if st.button("🔄 Recommencer le cours guidé"):
                        st.session_state.pop("plan_cours", None)
                        st.rerun()
                else:
                    st.progress((idx + 1) / len(plan))
                    st.markdown(f"**Étape {idx + 1} / {len(plan)} — {plan[idx]}**")

                    with st.expander("Voir le plan complet du cours"):
                        for i, etape in enumerate(plan):
                            icone = "✅" if i < idx else ("▶️" if i == idx else "⬜")
                            st.write(f"{icone} **Étape {i + 1} :** {etape}")

                    a_un_feedback = "guide_feedback_audio" in st.session_state
                    st.info(st.session_state["guide_texte"])
                    st.audio(st.session_state["guide_audio"], format="audio/mp3", autoplay=not a_un_feedback)

                    audio_guide = st.audio_input(
                        "🎤 Réponds à la question, pose une question, ou dis « plus simple » / « suivant » :",
                        key=f"guide_micro_{st.session_state['guide_tour_id']}"
                    )

                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        btn_repondre = st.button("✅ Répondre au prof", disabled=(audio_guide is None))
                    with col_b:
                        btn_plus_simple = st.button("💡 Explique plus simplement")
                    with col_c:
                        label_suiv = "➡️ Étape suivante" if idx + 1 < len(plan) else "🏁 Terminer le cours"
                        btn_suivant = st.button(label_suiv)

                    if btn_plus_simple:
                        with st.spinner("Je reformule avec une image plus simple..."):
                            generer_etape_guidee(
                                texte_cours, plan, idx,
                                gemini_key, groq_key, voix_id, vitesse_voix,
                                plus_simple=True
                            )
                            st.rerun()

                    if btn_suivant:
                        st.session_state["etape_index"] += 1
                        if st.session_state["etape_index"] < len(plan):
                            with st.spinner("Préparation de l'étape suivante..."):
                                generer_etape_guidee(
                                    texte_cours, plan, st.session_state["etape_index"],
                                    gemini_key, groq_key, voix_id, vitesse_voix
                                )
                        st.rerun()

                    if btn_repondre and audio_guide is not None:
                        with st.status("Le prof t'écoute...", expanded=True) as status:
                            try:
                                st.write("1/3 — Transcription de ta voix...")
                                texte_dit = transcrire_audio_groq(audio_guide.getvalue(), groq_key)
                                texte_lower = texte_dit.lower().strip()
                                nb_mots = len(texte_lower.split())

                                if nb_mots <= 5 and any(cmd in texte_lower for cmd in ["suivant", "étape suivante", "passe"]):
                                    st.session_state["etape_index"] += 1
                                    if st.session_state["etape_index"] < len(plan):
                                        generer_etape_guidee(
                                            texte_cours, plan, st.session_state["etape_index"],
                                            gemini_key, groq_key, voix_id, vitesse_voix
                                        )
                                    st.rerun()

                                if nb_mots <= 7 and any(cmd in texte_lower for cmd in ["plus simple", "pas compris", "répète", "explique autrement"]):
                                    generer_etape_guidee(
                                        texte_cours, plan, idx,
                                        gemini_key, groq_key, voix_id, vitesse_voix,
                                        plus_simple=True
                                    )
                                    st.rerun()

                                st.write("2/3 — Réponse du professeur...")
                                prompt_coach = (
                                    f"Tu es en pleine discussion orale avec un étudiant qui suit l'étape « {plan[idx]} ».\n"
                                    f"Ce que tu viens de lui expliquer et demander : {st.session_state['guide_texte']}\n"
                                    f"Ce que l'étudiant vient de te répondre au micro : {texte_dit}\n"
                                    f"Cours de référence : {texte_cours[:10000]}\n\n"
                                    f"Réponds-lui naturellement en 3 phrases courtes maximum (sans puces ni symboles bruts) : "
                                    f"si c'est une réponse à ta question, valide ce qu'il a compris et complète gentiment si besoin ; "
                                    f"s'il te pose une question, réponds-y avec une image simple. "
                                    f"Termine en lui disant s'il peut passer à l'étape suivante."
                                )
                                feedback, _ = generer_texte_ia(prompt_coach, gemini_key, groq_key)

                                st.write("3/3 — Génération de la voix...")
                                audio_fb, _ = texte_vers_mp3(feedback, voix_id, vitesse_voix)

                                st.session_state["guide_transcription"] = texte_dit
                                st.session_state["guide_feedback_texte"] = feedback.strip()
                                st.session_state["guide_feedback_audio"] = audio_fb
                                status.update(label="Réponse prête !", state="complete", expanded=False)
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))

                    if "guide_feedback_texte" in st.session_state:
                        st.caption(f"Ce que tu as dit : *« {st.session_state['guide_transcription']} »*")
                        st.success(f"**Réponse du prof :** {st.session_state['guide_feedback_texte']}")
                        st.audio(st.session_state["guide_feedback_audio"], format="audio/mp3", autoplay=True)

        # ==========================================
        # MODE 3 : CRASH-TEST & MÉMOIRE (+10 MIN)
        # ==========================================
        with tab_quiz:
            st.caption("Pour ancrer un cours déjà vu. Toute notion ratée revient automatiquement 10 minutes plus tard.")

            difficulte = st.radio(
                "Niveau de difficulté :",
                [
                    "🟢 Footing (Vrai/Faux & réponses en 3 mots)",
                    "🟠 Standard (Expliquer un mécanisme en 2 phrases)",
                    "🔴 Examen (Questions pièges & liens entre concepts)",
                ],
            )

            notions_pretes = [
                item for item in st.session_state["a_revoir"]
                if time.time() >= item["heure_rappel"]
            ]
            if notions_pretes:
                st.warning(f"🔁 {len(notions_pretes)} notion(s) ratée(s) il y a 10 min prête(s) à être réexpliquée(s) !")

            label_bouton = (
                "🔁 Réviser la notion ratée (Rappel +10 min)"
                if notions_pretes
                else "🎯 Poser une question"
            )

            if st.button(label_bouton):
                if not groq_key:
                    st.warning("Aucune clé API Groq configurée côté serveur.")
                else:
                    with st.status("Préparation de la question...", expanded=True) as status:
                        try:
                            if "Footing" in difficulte:
                                style_question = (
                                    "Pose une affirmation de type Vrai ou Faux (commence par 'Vrai ou faux : ...') "
                                    "OU une question très rapide qui se répond en 3 mots. Très accessible pour quelqu'un qui court."
                                )
                            elif "Standard" in difficulte:
                                style_question = (
                                    "Pose une question claire qui demande d'expliquer en 2 phrases un mécanisme ou un lien de cause à effet du cours."
                                )
                            else:
                                style_question = (
                                    "Pose une question exigeante de niveau examen : un cas limite, un piège classique ou un lien entre deux notions du cours."
                                )

                            maintenant = time.time()
                            index_a_reviser = next(
                                (i for i, item in enumerate(st.session_state["a_revoir"]) if maintenant >= item["heure_rappel"]),
                                None
                            )

                            if index_a_reviser is not None:
                                notion_ratee = st.session_state["a_revoir"].pop(index_a_reviser)
                                st.session_state["est_rappel"] = True
                                prompt_q = (
                                    f"Tu es un examinateur oral bienveillant. L'étudiant avait raté cette question il y a 10 minutes : "
                                    f"« {notion_ratee['question']} ».\n"
                                    f"Pose-lui à nouveau UNE SEULE question courte (2 phrases max) portant sur ce même concept, "
                                    f"reformulée légèrement. {style_question}\n"
                                    f"Règles : texte 100% oral sans puces ni symboles bruts.\n\n"
                                    f"Cours :\n{texte_cours[:12000]}"
                                )
                            else:
                                st.session_state["est_rappel"] = False
                                deja_posees = "\n".join(f"- {q}" for q in st.session_state["historique_questions"][-5:])
                                prompt_q = (
                                    f"Tu es un examinateur oral bienveillant mais précis. "
                                    f"Pose UNE SEULE question courte (2 phrases max) sur ce cours. {style_question}\n"
                                    f"Règles : texte 100% oral sans puces ni symboles bruts. "
                                    f"Évite de reposer ces questions déjà posées :\n{deja_posees}\n\n"
                                    f"Cours :\n{texte_cours[:12000]}"
                                )

                            st.write("1/2 — Formulation de la question...")
                            question_texte, _ = generer_texte_ia(prompt_q, gemini_key, groq_key)

                            st.write("2/2 — Génération de la voix...")
                            audio_q, _ = texte_vers_mp3(question_texte.strip(), voix_id, vitesse_voix)

                            st.session_state["q_id"] += 1
                            st.session_state["question_actuelle"] = question_texte.strip()
                            st.session_state["question_audio"] = audio_q
                            st.session_state["historique_questions"].append(question_texte.strip())
                            st.session_state.pop("feedback_audio", None)
                            st.session_state.pop("feedback_texte", None)
                            st.session_state.pop("transcription_user", None)
                            status.update(label="Question prête !", state="complete", expanded=False)
                            st.rerun()
                        except Exception as e:
                            status.update(label="Erreur", state="error")
                            st.error(str(e))

            if "question_actuelle" in st.session_state:
                a_un_feedback_quiz = "feedback_audio" in st.session_state
                if st.session_state["est_rappel"]:
                    st.warning(f"🔁 **Rappel mémoire (+10 min) :** {st.session_state['question_actuelle']}")
                else:
                    st.info(f"**Question :** {st.session_state['question_actuelle']}")
                st.audio(st.session_state["question_audio"], format="audio/mp3", autoplay=not a_un_feedback_quiz)

                audio_reponse = st.audio_input(
                    "🎤 Appuie sur le micro pour répondre à l'oral :",
                    key=f"micro_{st.session_state['q_id']}"
                )

                if audio_reponse is not None:
                    if st.button("✅ Envoyer mon explication"):
                        with st.status("Analyse de ta réponse...", expanded=True) as status:
                            try:
                                st.write("1/3 — Transcription de ta voix...")
                                texte_dit = transcrire_audio_groq(audio_reponse.getvalue(), groq_key)
                                st.session_state["transcription_user"] = texte_dit

                                st.write("2/3 — Évaluation par l'IA...")
                                prompt_eval = (
                                    f"Tu es un professeur chaleureux qui évalue la réponse orale d'un étudiant (niveau choisi : {difficulte}).\n"
                                    f"Question posée : {st.session_state['question_actuelle']}\n"
                                    f"Réponse de l'étudiant : {texte_dit}\n"
                                    f"Cours de référence : {texte_cours[:10000]}\n\n"
                                    f"Consignes strictes :\n"
                                    f"1. Sur la TOUTE PREMIÈRE ligne, écris uniquement 'STATUT: REUSSI' si la réponse est globalement juste, "
                                    f"ou 'STATUT: A_REVOIR' si elle est fausse ou trop incomplète.\n"
                                    f"2. À partir de la deuxième ligne, rédige ton retour oral en 3 phrases courtes et naturelles "
                                    f"(sans puces ni symboles bruts) : dis clairement si c'est vrai ou faux, valide ce qui est bon, "
                                    f"et complète de façon constructive et simple."
                                )
                                retour_brut, _ = generer_texte_ia(prompt_eval, gemini_key, groq_key)
                                lignes = retour_brut.strip().splitlines()
                                premiere_ligne = lignes[0].upper() if lignes else ""
                                est_a_revoir = "A_REVOIR" in premiere_ligne
                                retour_oral = "\n".join(lignes[1:]).strip() if len(lignes) > 1 else retour_brut

                                if est_a_revoir:
                                    st.session_state["a_revoir"].append({
                                        "question": st.session_state["question_actuelle"],
                                        "heure_rappel": time.time() + 600,
                                    })

                                st.write("3/3 — Enregistrement du retour vocal...")
                                audio_fb, _ = texte_vers_mp3(retour_oral, voix_id, vitesse_voix)

                                st.session_state["feedback_texte"] = retour_oral
                                st.session_state["feedback_statut"] = "À revoir dans 10 min" if est_a_revoir else "Validé"
                                st.session_state["feedback_audio"] = audio_fb
                                status.update(label="Correction prête !", state="complete", expanded=False)
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))

            if "feedback_texte" in st.session_state:
                st.caption(f"Ce que l'IA a entendu : *« {st.session_state['transcription_user']} »*")
                if st.session_state["feedback_statut"] == "Validé":
                    st.success(f"**Correction ({st.session_state['feedback_statut']}) :** {st.session_state['feedback_texte']}")
                else:
                    st.warning(f"**Correction ({st.session_state['feedback_statut']}) :** {st.session_state['feedback_texte']}")
                st.audio(st.session_state["feedback_audio"], format="audio/mp3", autoplay=True)

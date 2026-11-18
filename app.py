import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import io
import os
import re
import tempfile
import time
import unicodedata
import wave
import edge_tts
import streamlit as st
import streamlit.components.v1 as components
from pypdf import PdfReader
from google import genai
from google.genai import types
from groq import Groq
from supabase import create_client, Client

st.set_page_config(
    page_title="Echo",
    page_icon="logo.jpg",
    layout="centered",
    initial_sidebar_state="collapsed",
)

LOGO_URL = "https://raw.githubusercontent.com/thomaschappazrb-commits/appli-revision/main/logo.jpg"

components.html(
    f"""
    <script>
    try {{
        const doc = window.top.document;
        const fixerTitre = () => {{
            if (doc.title !== "Echo") {{
                doc.title = "Echo";
            }}
        }};
        fixerTitre();
        const baliseTitre = doc.querySelector("title");
        if (baliseTitre) {{
            new MutationObserver(fixerTitre).observe(baliseTitre, {{
                childList: true,
                characterData: true,
                subtree: true
            }});
        }}
        doc.querySelectorAll("link[rel*='icon']").forEach(el => el.remove());
        ["apple-touch-icon", "apple-touch-icon-precomposed", "icon"].forEach(rel => {{
            const link = doc.createElement("link");
            link.rel = rel;
            link.href = "{LOGO_URL}";
            doc.head.appendChild(link);
        }});
    }} catch (e) {{}}
    </script>
    """,
    height=0,
)

st.markdown(
    """
    <style>
    #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
        display: none !important;
    }
    header[data-testid="stHeader"] {
        background: transparent !important;
    }
    .block-container {
        padding-top: 1.3rem !important;
        padding-bottom: 3rem !important;
        max-width: 680px !important;
    }
    .echo-header {
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 12px 16px;
        background-color: #0F172A;
        border: 1px solid rgba(148, 163, 184, 0.16);
        border-radius: 16px;
        margin-bottom: 16px;
    }
    .echo-header img {
        width: 46px;
        height: 46px;
        border-radius: 11px;
        object-fit: cover;
    }
    .echo-title {
        font-size: 1.4rem;
        font-weight: 700;
        color: #F8FAFC;
        margin: 0;
        line-height: 1.15;
        letter-spacing: -0.02em;
    }
    .echo-sub {
        font-size: 0.84rem;
        color: #94A3B8;
        margin: 2px 0 0 0;
    }
    .echo-panel {
        background-color: #0F172A;
        border: 1px solid rgba(148, 163, 184, 0.15);
        border-radius: 14px;
        padding: 16px 18px;
        margin: 8px 0 14px 0;
    }
    .echo-panel-title {
        font-size: 1.02rem;
        font-weight: 600;
        color: #F8FAFC;
        margin: 0 0 4px 0;
        letter-spacing: -0.01em;
    }
    .echo-panel-desc {
        font-size: 0.86rem;
        color: #94A3B8;
        margin: 0;
        line-height: 1.45;
    }
    .echo-kicker {
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #38BDF8;
        margin-bottom: 4px;
    }
    .stButton > button {
        width: 100%;
        border-radius: 11px !important;
        padding: 0.56rem 0.9rem !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
        background-color: #1E293B !important;
        color: #F8FAFC !important;
        border: 1px solid rgba(148, 163, 184, 0.2) !important;
        transition: background-color 0.15s ease !important;
    }
    .stButton > button:hover {
        background-color: #334155 !important;
    }
    .stButton > button[kind="primary"] {
        background-color: #0284C7 !important;
        color: #FFFFFF !important;
        border: 1px solid #0284C7 !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #0369A1 !important;
    }
    .stDownloadButton > button {
        width: 100%;
        border-radius: 11px !important;
        padding: 0.56rem 0.9rem !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
        background-color: #1E293B !important;
        color: #F8FAFC !important;
        border: 1px solid rgba(148, 163, 184, 0.2) !important;
    }
    /* Zone de dépôt épurée en français */
    [data-testid="stFileUploader"] section {
        background-color: #0F172A !important;
        border: 1px dashed rgba(148, 163, 184, 0.32) !important;
        border-radius: 14px !important;
        padding: 14px 16px !important;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span {
        display: none !important;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] > div::before {
        content: "Glisser un ou plusieurs fichiers PDF ou TXT";
        font-size: 0.88rem;
        color: #CBD5E1;
        display: block;
    }
    [data-testid="stFileUploaderDropzoneInstructions"] small {
        display: none !important;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background-color: #0F172A;
        padding: 4px;
        border-radius: 12px;
        border: 1px solid rgba(148, 163, 184, 0.12);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 9px !important;
        padding: 7px 14px !important;
        font-weight: 600 !important;
        font-size: 0.88rem !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def lire_secret(nom: str) -> str:
    try:
        return st.secrets[nom]
    except Exception:
        return ""


def nettoyer_cle(cle: str) -> str:
    return cle.strip().encode("ascii", "ignore").decode("ascii")


# ==========================================
# CONNEXION SUPABASE (AUTH + STOCKAGE)
# ==========================================
SUPABASE_URL = lire_secret("SUPABASE_URL").strip()
SUPABASE_KEY = nettoyer_cle(lire_secret("SUPABASE_KEY"))
SUPABASE_ACTIF = bool(SUPABASE_URL and SUPABASE_KEY)


def get_supabase() -> Client | None:
    if not SUPABASE_ACTIF:
        return None
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    sess = st.session_state.get("sb_session")
    if sess:
        try:
            client.auth.set_session(sess["access_token"], sess["refresh_token"])
        except Exception:
            pass
    return client


def sauvegarder_podcast_cloud(
    titre: str,
    filename: str,
    audio_bytes: bytes,
    mime_type: str,
    info: str,
    script: str,
    duree_sec: int,
) -> tuple[bool, str]:
    user = st.session_state.get("sb_user")
    client = get_supabase()
    if not user or not client:
        return False, "Non connecté"

    ext = "wav" if "wav" in mime_type else "mp3"
    nom_complet = f"{filename}.{ext}"
    storage_path = f"{user['id']}/{int(time.time())}_{nom_complet}"

    try:
        client.storage.from_("podcasts").upload(
            path=storage_path,
            file=audio_bytes,
            file_options={"content-type": mime_type, "upsert": "true"},
        )
        client.table("podcasts").insert(
            {
                "user_id": user["id"],
                "titre": titre,
                "filename": nom_complet,
                "storage_path": storage_path,
                "mime_type": mime_type,
                "info": info,
                "script": script,
                "duree_sec": duree_sec,
            }
        ).execute()
        return True, "Enregistré dans la bibliothèque."
    except Exception as e:
        return False, f"Erreur d'enregistrement : {e}"


def lister_podcasts_cloud() -> list[dict]:
    user = st.session_state.get("sb_user")
    client = get_supabase()
    if not user or not client:
        return []
    try:
        rep = (
            client.table("podcasts")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .execute()
        )
        return rep.data or []
    except Exception:
        return []


def supprimer_podcast_cloud(item: dict) -> bool:
    client = get_supabase()
    if not client:
        return False
    try:
        client.storage.from_("podcasts").remove([item["storage_path"]])
    except Exception:
        pass
    try:
        client.table("podcasts").delete().eq("id", item["id"]).execute()
        st.session_state.get("biblio_audio_cache", {}).pop(item["id"], None)
        return True
    except Exception:
        return False


def telecharger_audio_cloud(item: dict) -> bytes | None:
    cache = st.session_state.setdefault("biblio_audio_cache", {})
    if item["id"] in cache:
        return cache[item["id"]]
    client = get_supabase()
    if not client:
        return None
    try:
        data = client.storage.from_("podcasts").download(item["storage_path"])
        cache[item["id"]] = data
        return data
    except Exception:
        return None


def formater_duree(sec: int) -> str:
    if not sec or sec <= 0:
        return ""
    m, s = divmod(int(sec), 60)
    return f"{m} min {s:02d}"


# ==========================================
# MOTEURS IA & SYNTHÈSE VOCALE
# ==========================================
MODELES_GEMINI_TEXTE = [
    "gemini-3.8-flash",
    "gemini-3.8-flash-lite",
    "gemini-flash-latest",
]

MODELES_GEMINI_TTS = [
    "gemini-3.8-flash-tts",
    "gemini-2.5-flash-preview-tts",
]

VOIX_EDGE = {
    "Vivienne (Femme — fluide)": "fr-FR-VivienneMultilingualNeural",
    "Rémy (Homme — posé)": "fr-FR-RemyMultilingualNeural",
    "Denise (Femme — claire)": "fr-FR-DeniseNeural",
    "Henri (Homme — classique)": "fr-FR-HenriNeural",
}

VOIX_PROF_EDGE = "fr-FR-RemyMultilingualNeural"
VOIX_ELEVE_EDGE = "fr-FR-VivienneMultilingualNeural"


def nettoyer_texte_pour_oral(texte: str) -> str:
    texte = re.sub(r"[*_#`~]", "", texte)
    texte = re.sub(r"\s+", " ", texte)
    return texte.strip()


def _silence_mp3(duree_ms: int) -> bytes:
    nb_frames = max(1, duree_ms // 24)
    frame_vide = b"\xff\xf3\x64\xc4" + (b"\x00" * 140)
    return frame_vide * nb_frames


def _pcm_vers_wav(pcm_bytes: bytes, sample_rate: int = 24000) -> bytes:
    if pcm_bytes[:4] == b"RIFF":
        return pcm_bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


async def _ecrire_edge_async(texte: str, voix: str, vitesse: str, chemin_mp3: str):
    communicate = edge_tts.Communicate(texte, voix, rate=vitesse)
    await communicate.save(chemin_mp3)


def _segment_edge_tts(texte: str, voix: str, vitesse: str) -> bytes:
    texte_propre = nettoyer_texte_pour_oral(texte)
    if not texte_propre:
        return b""

    derniere_erreur = None
    for tentative in range(3):
        tmp_mp3 = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f_mp3:
                tmp_mp3 = f_mp3.name

            asyncio.run(
                asyncio.wait_for(
                    _ecrire_edge_async(texte_propre, voix, vitesse, tmp_mp3),
                    timeout=35.0,
                )
            )

            if os.path.exists(tmp_mp3) and os.path.getsize(tmp_mp3) > 0:
                with open(tmp_mp3, "rb") as f:
                    return f.read()
            derniere_erreur = "fichier MP3 vide"
        except Exception as e:
            derniere_erreur = str(e) or type(e).__name__
        finally:
            if tmp_mp3 and os.path.exists(tmp_mp3):
                try:
                    os.remove(tmp_mp3)
                except OSError:
                    pass
        time.sleep(0.6 * (tentative + 1))

    raise RuntimeError(f"Synthèse vocale indisponible : {derniere_erreur}")


def _synthese_gemini_tts(script: str, gemini_key: str, est_duo: bool, vitesse: str) -> tuple[bytes, str]:
    client = genai.Client(api_key=gemini_key)
    indication_rythme = (
        "Lis à un rythme posé, calme et articulé, avec des respirations naturelles."
        if "-" in vitesse
        else "Lis d'un ton naturel, fluide et engagé."
    )

    if est_duo:
        speech_cfg = types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker="PROF",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Orus")
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker="ELEVE",
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                        ),
                    ),
                ]
            )
        )
    else:
        speech_cfg = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        )

    lignes = [l.strip() for l in script.splitlines() if l.strip()]
    blocs = []
    courant = []
    taille = 0
    for l in lignes:
        if taille + len(l) > 2800 and courant:
            blocs.append("\n".join(courant))
            courant = [l]
            taille = len(l)
        else:
            courant.append(l)
            taille += len(l)
    if courant:
        blocs.append("\n".join(courant))

    pcm_total = bytearray()
    for bloc in blocs:
        prompt_tts = f"{indication_rythme}\n\n{bloc}"
        succes_bloc = False
        for modele in MODELES_GEMINI_TTS:
            try:
                rep = client.models.generate_content(
                    model=modele,
                    contents=prompt_tts,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=speech_cfg,
                    ),
                )
                part = rep.candidates[0].content.parts[0]
                data = part.inline_data.data
                if data:
                    if data[:4] == b"RIFF":
                        pcm_total.extend(data[44:])
                    else:
                        pcm_total.extend(data)
                    succes_bloc = True
                    break
            except Exception:
                continue
        if not succes_bloc:
            raise RuntimeError("Synthèse studio indisponible.")

    return _pcm_vers_wav(bytes(pcm_total)), "2 voix" if est_duo else "1 voix"


def synthese_audio_solo(
    texte: str,
    gemini_key: str,
    voix_edge: str,
    vitesse: str,
    barre_progression=None,
) -> tuple[bytes, str, str]:
    if gemini_key:
        try:
            wav_bytes, info = _synthese_gemini_tts(nettoyer_texte_pour_oral(texte), gemini_key, False, vitesse)
            if barre_progression is not None:
                barre_progression.progress(1.0)
            return wav_bytes, "audio/wav", info
        except Exception:
            pass

    phrases = [p.strip() for p in re.split(r"(?<=[.!?])\s+", nettoyer_texte_pour_oral(texte)) if p.strip()]
    if len(phrases) <= 1:
        mp3 = _segment_edge_tts(texte, voix_edge, vitesse)
        if barre_progression is not None:
            barre_progression.progress(1.0)
        return mp3, "audio/mp3", "1 voix"

    total = len(phrases)
    segments = [b""] * total

    def tache_phrase(idx_p):
        idx, p = idx_p
        return idx, _segment_edge_tts(p, voix_edge, vitesse)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for done, (idx, seg) in enumerate(pool.map(tache_phrase, enumerate(phrases)), start=1):
            segments[idx] = seg
            if barre_progression is not None:
                barre_progression.progress(done / total)

    pause = _silence_mp3(260)
    assemblage = bytearray()
    for i, seg in enumerate(segments):
        assemblage.extend(seg)
        if i < total - 1:
            assemblage.extend(pause)
    return bytes(assemblage), "audio/mp3", "1 voix"


def synthese_audio_dialogue(
    script_brut: str,
    gemini_key: str,
    vitesse: str,
    barre_progression=None,
) -> tuple[bytes, str, str]:
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
        return synthese_audio_solo(script_brut, gemini_key, VOIX_PROF_EDGE, vitesse, barre_progression)

    if gemini_key:
        try:
            script_propre = "\n".join(f"{r}: {txt}" for r, txt in lignes_dialogue)
            if barre_progression is not None:
                barre_progression.progress(0.4)
            wav_bytes, info = _synthese_gemini_tts(script_propre, gemini_key, True, vitesse)
            if barre_progression is not None:
                barre_progression.progress(1.0)
            return wav_bytes, "audio/wav", info
        except Exception:
            pass

    total = len(lignes_dialogue)
    segments = [b""] * total

    def tache_segment(idx_item):
        idx, (role, replique) = idx_item
        voix = VOIX_PROF_EDGE if role == "PROF" else VOIX_ELEVE_EDGE
        return idx, _segment_edge_tts(replique, voix, vitesse)

    with ThreadPoolExecutor(max_workers=2) as pool:
        for done, (idx, seg) in enumerate(pool.map(tache_segment, enumerate(lignes_dialogue)), start=1):
            segments[idx] = seg
            if barre_progression is not None:
                barre_progression.progress(done / total)

    pause_tour = _silence_mp3(480)
    audio_complet = bytearray()
    for i, seg in enumerate(segments):
        audio_complet.extend(seg)
        if i < total - 1:
            audio_complet.extend(pause_tour)

    return bytes(audio_complet), "audio/mp3", "2 voix"


def generer_texte_ia(prompt: str, gemini_key: str, groq_key: str, temperature: float = 0.45) -> tuple[str, str]:
    erreurs = []

    if gemini_key:
        client_gemini = genai.Client(api_key=gemini_key)
        for modele in MODELES_GEMINI_TEXTE:
            try:
                def req():
                    return client_gemini.models.generate_content(
                        model=modele,
                        contents=prompt,
                        config=types.GenerateContentConfig(temperature=temperature),
                    )

                with ThreadPoolExecutor(max_workers=1) as ex:
                    rep = ex.submit(req).result(timeout=25)
                if rep.text:
                    return rep.text, modele
            except Exception as e:
                erreurs.append(f"{modele}: {e}")

    if groq_key:
        try:
            client_groq = Groq(api_key=groq_key, timeout=28.0)
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
                        temperature=temperature,
                    )
                    return chat.choices[0].message.content, modele_groq
                except Exception as e:
                    erreurs.append(f"{modele_groq}: {e}")
        except Exception as e:
            erreurs.append(str(e))

    raise RuntimeError("Service temporairement indisponible.\n" + "\n".join(erreurs))


def construire_carte_cours(texte_complet: str, gemini_key: str, groq_key: str) -> str:
    texte_propre = re.sub(r"\n{3,}", "\n\n", texte_complet).strip()
    if len(texte_propre) <= 18000:
        return texte_propre

    taille_bloc = 16000
    blocs = [texte_propre[i:i + taille_bloc] for i in range(0, len(texte_propre), taille_bloc)][:10]

    def analyser_bloc(idx_bloc):
        idx, contenu = idx_bloc
        prompt = (
            f"Extrais la matière pédagogique essentielle de la partie {idx + 1}/{len(blocs)} de ces documents.\n"
            f"Conserve les repères '=== DOCUMENT : ... ===' s'ils apparaissent et liste de manière dense :\n"
            f"1. Les concepts et définitions clés\n"
            f"2. Les mécanismes et relations de cause à effet\n"
            f"3. Les formules ou lois importantes traduites en mots\n"
            f"4. Les ordres de grandeur, cas limites ou pièges classiques.\n"
            f"Sois exhaustif et rigoureux, sans phrase d'introduction.\n\n"
            f"Extrait :\n{contenu}"
        )
        synthese, _ = generer_texte_ia(prompt, gemini_key, groq_key, temperature=0.2)
        return f"--- PARTIE {idx + 1} ---\n{synthese.strip()}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        parties = list(pool.map(analyser_bloc, enumerate(blocs)))

    return "\n\n".join(parties)


def generer_titre_episode(matiere: str, noms_fichiers: list[str], gemini_key: str, groq_key: str) -> tuple[str, str]:
    prompt = (
        f"Donne un titre court, sobre et précis (3 à 6 mots maximum) qui résume le sujet principal de ces documents.\n"
        f"Documents : {', '.join(noms_fichiers)}\n"
        f"Contenu :\n{matiere[:8000]}\n\n"
        f"Réponds UNIQUEMENT par le titre, sans guillemets, sans point final, sans préfixe."
    )
    try:
        titre_brut, _ = generer_texte_ia(prompt, gemini_key, groq_key, temperature=0.3)
        titre_propre = re.sub(r"[\"'«»\n\r]", "", titre_brut).strip(" .:-")
        titre_propre = titre_propre[:70] if titre_propre else "Synthèse de cours"
    except Exception:
        titre_propre = "Synthèse de cours"

    sans_accents = unicodedata.normalize("NFKD", titre_propre).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", sans_accents.lower()).strip("_")
    slug = slug[:48] or "synthese"
    return titre_propre, f"echo_{slug}"


def generer_script_podcast_long(
    matiere_cours: str,
    duree_minutes: int,
    est_duo: bool,
    gemini_key: str,
    groq_key: str,
    zone_statut=None,
) -> tuple[str, str]:
    mots_cibles = duree_minutes * 140
    nb_actes = max(1, round(duree_minutes / 6))

    regles_base = (
        "RÈGLES DE STYLE ORATOIRE STRICTES :\n"
        "- INTERDICTION des formules creuses et tics de langage artificiels ('Bonjour', 'Bienvenue', 'En effet', 'Tout à fait', "
        "'C'est une excellente question', 'C'est fascinant', 'En somme', 'Il est crucial de noter').\n"
        "- Parle comme deux chercheurs ou étudiants avancés qui discutent naturellement sans script rigide.\n"
        "- Explique les mécanismes en profondeur avec des images mentales concrètes.\n"
        "- Traduis toute formule ou loi mathématique en français parlé clair (jamais de symboles bruts).\n"
        "- Aucun astérisque, aucune liste à puces, aucun titre ni markdown : uniquement du texte destiné à être lu à voix haute."
    )
    format_voix = (
        "FORMAT STRICT : chaque ligne commence exactement par 'PROF:' ou 'ELEVE:' suivi du texte parlé.\n"
        "PROF explique avec clarté et précision ; ELEVE pose des questions directes, "
        "reformule avec ses mots et fait le lien entre les notions."
        if est_duo
        else "FORMAT STRICT : paragraphes fluides et naturels pour un seul narrateur."
    )

    if nb_actes == 1:
        if zone_statut:
            zone_statut.write(f"Rédaction du script ({duree_minutes} min)...")
        prompt = (
            f"Écris un script audio pédagogique en français d'environ {mots_cibles} mots pour une écoute sans support visuel.\n"
            f"Attaque dès la 1re phrase par le problème concret que ce cours résout, déroule les mécanismes clés puis termine par 2 pièges classiques.\n"
            f"{regles_base}\n{format_voix}\n\n"
            f"Matière des documents :\n{matiere_cours[:36000]}"
        )
        return generer_texte_ia(prompt, gemini_key, groq_key, temperature=0.5)

    if zone_statut:
        zone_statut.write(f"Structuration en {nb_actes} parties...")

    prompt_plan = (
        f"Tu prépares un épisode audio approfondi de {duree_minutes} minutes couvrant l'intégralité des documents fournis.\n"
        f"Découpe l'épisode en exactement {nb_actes} parties chronologiques et complémentaires, "
        f"en répartissant équitablement toutes les notions du début à la fin.\n"
        f"FORMAT STRICT : uniquement {nb_actes} lignes numérotées (1. ... à {nb_actes}. ...), détaillant les notions de chaque partie.\n\n"
        f"Matière :\n{matiere_cours[:40000]}"
    )
    plan_brut, modele_txt = generer_texte_ia(prompt_plan, gemini_key, groq_key, temperature=0.3)
    actes = [
        re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", l).strip()
        for l in plan_brut.splitlines()
        if len(re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", l).strip()) > 5
    ][:nb_actes]
    while len(actes) < nb_actes:
        actes.append(f"Approfondissement — partie {len(actes) + 1}")

    mots_par_acte = max(650, mots_cibles // nb_actes)
    repliques_par_acte = max(12, mots_par_acte // 55)
    morceaux_script = []
    derniere_transition = ""

    for i, sujet_acte in enumerate(actes):
        if zone_statut:
            zone_statut.write(f"Rédaction partie {i + 1}/{nb_actes} : {sujet_acte[:55]}...")

        if i == 0:
            consigne_position = (
                "C'est le DÉBUT de l'épisode : attaque dès la première phrase par le problème concret résolu par ce cours, "
                "annonce rapidement le fil conducteur, puis développe cette Partie 1. Ne conclus pas l'épisode à la fin."
            )
        elif i == nb_actes - 1:
            consigne_position = (
                f"C'est la DERNIÈRE partie ({i + 1}/{nb_actes}). Enchaîne directement dans la continuité de la partie précédente "
                f"(sans salutation ni réintroduction), développe cette partie, puis termine par les pièges d'examen et une conclusion sobre.\n"
                f"Dernières lignes de la partie précédente :\n{derniere_transition}"
            )
        else:
            consigne_position = (
                f"C'est la partie intermédiaire {i + 1}/{nb_actes}. Enchaîne directement dans la continuité de la partie précédente "
                f"(sans salutation ni réintroduction, et sans conclure l'épisode), en approfondissant chaque détail technique.\n"
                f"Dernières lignes de la partie précédente :\n{derniere_transition}"
            )

        cible_acte = (
            f"environ {mots_par_acte} mots (soit {repliques_par_acte} à {repliques_par_acte + 4} répliques fouillées de 3 à 4 phrases chacune)"
            if est_duo
            else f"environ {mots_par_acte} mots rédigés en paragraphes riches et détaillés"
        )

        prompt_acte = (
            f"Tu rédiges la Partie {i + 1} sur {nb_actes} d'un épisode audio universitaire de {duree_minutes} minutes.\n"
            f"Plan complet :\n" + "\n".join(f"{k + 1}. {a}" for k, a in enumerate(actes)) + "\n\n"
            f"SUJET EXCLUSIF DE CETTE PARTIE {i + 1} : {sujet_acte}\n"
            f"{consigne_position}\n"
            f"Longueur cible pour cette partie : {cible_acte}. Explique les mécanismes, "
            f"les relations entre grandeurs, les cas concrets et les subtilités.\n"
            f"{regles_base}\n{format_voix}\n\n"
            f"Matière des documents :\n{matiere_cours[:40000]}"
        )

        texte_acte, modele_txt = generer_texte_ia(prompt_acte, gemini_key, groq_key, temperature=0.5)
        texte_acte = texte_acte.strip()
        morceaux_script.append(texte_acte)
        lignes_acte = [l for l in texte_acte.splitlines() if l.strip()]
        derniere_transition = "\n".join(lignes_acte[-3:])

    return "\n\n".join(morceaux_script), modele_txt


def transcrire_audio_groq(audio_bytes: bytes, groq_key: str) -> str:
    client_groq = Groq(api_key=groq_key, timeout=15.0)
    modeles_whisper = [
        m.id for m in client_groq.models.list().data
        if getattr(m, "active", True) and "whisper" in m.id.lower()
    ]
    if not modeles_whisper:
        raise RuntimeError("Transcription vocale indisponible.")
    modeles_whisper.sort(key=lambda x: 0 if "turbo" in x.lower() else 1)
    transcription = client_groq.audio.transcriptions.create(
        file=("reponse.wav", audio_bytes),
        model=modeles_whisper[0],
        language="fr",
    )
    return transcription.text


def generer_etape_guidee(
    matiere_cours: str,
    plan: list[str],
    index_etape: int,
    gemini_key: str,
    groq_key: str,
    voix_edge: str,
    vitesse_voix: str,
    plus_simple: bool = False,
):
    etape_actuelle = plan[index_etape]
    numero = index_etape + 1
    total = len(plan)

    if plus_simple:
        consigne = (
            f"Reformule l'étape {numero}/{total} (« {etape_actuelle} ») beaucoup plus simplement, sans formule ni jargon, "
            f"pour une écoute sans support visuel.\n"
            f"Utilise une analogie concrète en 75 mots maximum, avec des phrases courtes.\n"
            f"Termine par une question courte pour vérifier que l'image mentale est claire."
        )
    else:
        consigne = (
            f"Explique à l'oral cette notion à un étudiant qui écoute sans schéma.\n"
            f"Étape {numero}/{total} : « {etape_actuelle} ».\n"
            f"Explique uniquement cette notion en 95 mots maximum.\n"
            f"Règles :\n"
            f"- Attaque directement le sujet dès la première phrase (sans salutation).\n"
            f"- Donne d'abord l'intuition physique ou logique, puis le mécanisme précis.\n"
            f"- Traduis toute relation mathématique en français parlé.\n"
            f"- Termine par une micro-question courte pour vérifier la compréhension."
        )

    prompt = f"{consigne}\n\nMatière du cours :\n{matiere_cours[:28000]}"
    explication, _ = generer_texte_ia(prompt, gemini_key, groq_key)
    audio_etape, mime_etape, _ = synthese_audio_solo(explication, gemini_key, voix_edge, vitesse_voix)

    st.session_state["guide_tour_id"] += 1
    st.session_state["guide_texte"] = explication.strip()
    st.session_state["guide_audio"] = audio_etape
    st.session_state["guide_mime"] = mime_etape
    st.session_state.pop("guide_feedback_texte", None)
    st.session_state.pop("guide_feedback_audio", None)
    st.session_state.pop("guide_transcription", None)


def reinitialiser_cours():
    cles = [
        "carte_cours", "nb_pages_pdf", "noms_fichiers",
        "podcast_audio", "podcast_mime", "podcast_script", "podcast_info",
        "podcast_titre", "podcast_filename", "podcast_sauvegarde_msg",
        "plan_cours", "etape_index", "guide_texte", "guide_audio", "guide_mime",
        "guide_feedback_texte", "guide_feedback_audio", "guide_feedback_mime", "guide_transcription",
        "question_actuelle", "question_audio", "question_mime",
        "feedback_texte", "feedback_audio", "feedback_mime", "transcription_user",
        "last_guide_audio_hash", "last_quiz_audio_hash",
    ]
    for c in cles:
        st.session_state.pop(c, None)
    st.session_state["historique_questions"] = []
    st.session_state["a_revoir"] = []
    st.session_state["est_rappel"] = False


for cle, val in [
    ("q_id", 0),
    ("guide_tour_id", 0),
    ("historique_questions", []),
    ("a_revoir", []),
    ("est_rappel", False),
    ("mode_invite", False),
]:
    if cle not in st.session_state:
        st.session_state[cle] = val

groq_key = nettoyer_cle(lire_secret("GROQ_API_KEY"))
gemini_key = nettoyer_cle(lire_secret("GEMINI_API_KEY"))

# ==========================================
# EN-TÊTE SOBRE + COMPTE & AUDIO
# ==========================================
col_hero, col_cfg = st.columns([4.2, 1.2], vertical_alignment="center")
with col_hero:
    st.markdown(
        f"""
        <div class="echo-header">
            <img src="{LOGO_URL}" alt="Echo">
            <div>
                <p class="echo-title">Echo</p>
                <p class="echo-sub">Révision audio</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with col_cfg:
    with st.popover("Réglages"):
        vitesse_voix = st.select_slider(
            "Vitesse de lecture",
            options=["-20%", "-12%", "-6%", "+0%", "+8%"],
            value="-12%",
        )
        choix_voix = st.selectbox("Voix solo", list(VOIX_EDGE.keys()))
        voix_edge = VOIX_EDGE[choix_voix]

        if "sb_user" in st.session_state:
            st.divider()
            st.caption(st.session_state["sb_user"]["email"])
            if st.button("Se déconnecter"):
                client_sb = get_supabase()
                if client_sb:
                    try:
                        client_sb.auth.sign_out()
                    except Exception:
                        pass
                for k in ("sb_user", "sb_session", "biblio_audio_cache"):
                    st.session_state.pop(k, None)
                st.session_state["mode_invite"] = False
                st.rerun()
        elif st.session_state.get("mode_invite") and SUPABASE_ACTIF:
            st.divider()
            if st.button("Se connecter"):
                st.session_state["mode_invite"] = False
                st.rerun()

        if not groq_key and not gemini_key:
            groq_key = nettoyer_cle(st.text_input("Clé Groq", type="password"))
            gemini_key = nettoyer_cle(st.text_input("Clé Gemini", type="password"))

# ==========================================
# ÉCRAN DE CONNEXION / INSCRIPTION ÉPURÉ
# ==========================================
if SUPABASE_ACTIF and "sb_user" not in st.session_state and not st.session_state["mode_invite"]:
    st.markdown(
        """
        <div class="echo-panel">
            <div class="echo-kicker">Compte</div>
            <p class="echo-panel-title">Accéder à ta bibliothèque</p>
            <p class="echo-panel-desc">
                Connecte-toi pour retrouver tes épisodes enregistrés, les écouter ou les télécharger hors-ligne.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tab_login, tab_signup = st.tabs(["Connexion", "Inscription"])

    with tab_login:
        with st.form("form_login"):
            email_in = st.text_input("Adresse email")
            pass_in = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Continuer", type="primary"):
                if not email_in or not pass_in:
                    st.warning("Renseigne ton email et ton mot de passe.")
                else:
                    try:
                        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
                        res = sb.auth.sign_in_with_password({"email": email_in.strip(), "password": pass_in})
                        if res.user and res.session:
                            st.session_state["sb_user"] = {"id": res.user.id, "email": res.user.email}
                            st.session_state["sb_session"] = {
                                "access_token": res.session.access_token,
                                "refresh_token": res.session.refresh_token,
                            }
                            st.rerun()
                    except Exception as e:
                        st.error(f"Identifiants invalides : {e}")

    with tab_signup:
        with st.form("form_signup"):
            email_up = st.text_input("Adresse email")
            pass_up = st.text_input("Mot de passe (6 caractères minimum)", type="password")
            if st.form_submit_button("Créer un compte", type="primary"):
                if not email_up or len(pass_up) < 6:
                    st.warning("Entre un email valide et un mot de passe d'au moins 6 caractères.")
                else:
                    try:
                        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
                        res = sb.auth.sign_up({"email": email_up.strip(), "password": pass_up})
                        if res.user and res.session:
                            st.session_state["sb_user"] = {"id": res.user.id, "email": res.user.email}
                            st.session_state["sb_session"] = {
                                "access_token": res.session.access_token,
                                "refresh_token": res.session.refresh_token,
                            }
                            st.rerun()
                        else:
                            st.info("Compte créé. Connecte-toi dans l'onglet Connexion.")
                    except Exception as e:
                        st.error(f"Création impossible : {e}")

    if st.button("Continuer sans compte"):
        st.session_state["mode_invite"] = True
        st.rerun()

    st.stop()


# ==========================================
# AFFICHAGE DE LA BIBLIOTHÈQUE
# ==========================================
def afficher_bibliotheque():
    if "sb_user" not in st.session_state:
        st.info("Connecte-toi via Réglages pour sauvegarder et retrouver tes épisodes.")
        return

    episodes = lister_podcasts_cloud()
    if not episodes:
        st.markdown(
            """
            <div class="echo-panel">
                <p class="echo-panel-title">Aucun épisode enregistré</p>
                <p class="echo-panel-desc">
                    Importe un cours ci-dessus puis génère un podcast : il sera conservé ici automatiquement.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    for ep in episodes:
        try:
            date_txt = datetime.fromisoformat(ep["created_at"].replace("Z", "+00:00")).strftime("%d/%m/%Y")
        except Exception:
            date_txt = ""
        duree_txt = formater_duree(ep.get("duree_sec", 0))
        meta = " • ".join(x for x in [date_txt, duree_txt, ep.get("info", "")] if x)

        st.markdown(
            f"""
            <div class="echo-panel" style="margin-bottom:8px;">
                <p class="echo-panel-title">{ep['titre']}</p>
                <p class="echo-panel-desc">{meta}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        audio_cache = st.session_state.get("biblio_audio_cache", {}).get(ep["id"])
        col1, col2, col3 = st.columns([1.3, 1.3, 1])
        with col1:
            if st.button("Écouter", key=f"play_{ep['id']}"):
                with st.spinner("Chargement..."):
                    telecharger_audio_cloud(ep)
                st.rerun()
        with col2:
            if audio_cache:
                st.download_button(
                    label="Télécharger",
                    data=audio_cache,
                    file_name=ep["filename"],
                    mime=ep.get("mime_type", "audio/mp3"),
                    key=f"dl_{ep['id']}",
                )
            else:
                if st.button("Exporter MP3", key=f"prep_{ep['id']}"):
                    with st.spinner("Préparation..."):
                        telecharger_audio_cloud(ep)
                    st.rerun()
        with col3:
            if st.button("Supprimer", key=f"del_{ep['id']}"):
                if supprimer_podcast_cloud(ep):
                    st.rerun()
                else:
                    st.error("Suppression impossible.")

        if audio_cache:
            st.audio(audio_cache, format=ep.get("mime_type", "audio/mp3"))

        if ep.get("script"):
            with st.expander("Transcription"):
                st.write(ep["script"])


if st.session_state["a_revoir"]:
    maintenant = time.time()
    pretes = sum(1 for item in st.session_state["a_revoir"] if maintenant >= item["heure_rappel"])
    col_mem1, col_mem2 = st.columns([3.2, 1.2], vertical_alignment="center")
    with col_mem1:
        st.caption(f"Notions à revoir : {len(st.session_state['a_revoir'])} ({pretes} prête(s))")
    with col_mem2:
        if st.button("Avancer 10 min", key="sim_rappel"):
            for item in st.session_state["a_revoir"]:
                item["heure_rappel"] = time.time() - 1
            st.rerun()

# ==========================================
# ZONE DE DÉPÔT MULTI-DOCUMENTS
# ==========================================
uploaded_files = st.file_uploader(
    "Importer un cours",
    type=["pdf", "txt"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

if not uploaded_files:
    if "sb_user" in st.session_state:
        st.markdown('<div class="echo-kicker" style="margin-top:14px;">Bibliothèque</div>', unsafe_allow_html=True)
        afficher_bibliotheque()
else:
    signature_fichiers = tuple((f.name, f.size) for f in uploaded_files)
    if st.session_state.get("fichiers_actuels") != signature_fichiers:
        reinitialiser_cours()
        st.session_state["fichiers_actuels"] = signature_fichiers

    morceaux_docs = []
    nb_pages = 0
    noms_fichiers = []
    for f in uploaded_files:
        noms_fichiers.append(f.name)
        if f.name.lower().endswith(".pdf"):
            reader = PdfReader(f)
            nb_pages += len(reader.pages)
            contenu = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            nb_pages += 1
            contenu = f.read().decode("utf-8", errors="ignore")
        if contenu.strip():
            morceaux_docs.append(f"=== DOCUMENT : {f.name} ===\n{contenu.strip()}")

    texte_brut = "\n\n".join(morceaux_docs)

    if not texte_brut.strip():
        st.warning("Aucun texte détecté dans les fichiers importés.")
    else:
        if "carte_cours" not in st.session_state:
            label_analyse = f"Indexation de {len(noms_fichiers)} document(s) ({nb_pages} page(s))..."
            with st.status(label_analyse, expanded=False) as st_load:
                try:
                    st.session_state["carte_cours"] = construire_carte_cours(texte_brut, gemini_key, groq_key)
                    st.session_state["nb_pages_pdf"] = nb_pages
                    st.session_state["noms_fichiers"] = noms_fichiers
                    st_load.update(
                        label=f"{len(noms_fichiers)} document(s) prêt(s) ({nb_pages} page(s))",
                        state="complete",
                    )
                except Exception as e:
                    st_load.update(label="Erreur de lecture", state="error")
                    st.error(str(e))

        matiere_cours = st.session_state.get("carte_cours", texte_brut[:32000])

        tab_podcast, tab_guide, tab_quiz, tab_biblio = st.tabs([
            "Podcast",
            "Parcours",
            "Quiz",
            "Bibliothèque",
        ])

        # ==========================================
        # MODE 1 : PODCAST LONG + SAUVEGARDE AUTO
        # ==========================================
        with tab_podcast:
            if hasattr(st, "segmented_control"):
                format_audio = st.segmented_control(
                    "Format",
                    ["Dialogue (2 voix)", "Monologue (1 voix)"],
                    default="Dialogue (2 voix)",
                ) or "Dialogue (2 voix)"
            else:
                format_audio = st.radio("Format", ["Dialogue (2 voix)", "Monologue (1 voix)"], horizontal=True)

            duree_cible = st.slider(
                "Durée cible (minutes)",
                min_value=5,
                max_value=30,
                value=12,
                step=1,
            )

            if st.button("Générer l'épisode", type="primary"):
                if not gemini_key and not groq_key:
                    st.warning("Clé API manquante.")
                else:
                    with st.status(f"Génération de l'épisode (~{duree_cible} min)...", expanded=True) as status:
                        try:
                            est_duo = "2 voix" in format_audio

                            script_audio, _ = generer_script_podcast_long(
                                matiere_cours,
                                duree_cible,
                                est_duo,
                                gemini_key,
                                groq_key,
                                zone_statut=status,
                            )

                            status.write("Création du titre...")
                            titre_ep, base_filename = generer_titre_episode(
                                matiere_cours,
                                st.session_state.get("noms_fichiers", noms_fichiers),
                                gemini_key,
                                groq_key,
                            )

                            status.write("Synthèse vocale...")
                            barre = st.progress(0.0)
                            if est_duo:
                                audio_bytes, mime_audio, info_voix = synthese_audio_dialogue(
                                    script_audio, gemini_key, vitesse_voix, barre
                                )
                            else:
                                audio_bytes, mime_audio, info_voix = synthese_audio_solo(
                                    script_audio, gemini_key, voix_edge, vitesse_voix, barre
                                )

                            duree_reelle_sec = (
                                len(audio_bytes) // 48000 if "wav" in mime_audio else len(audio_bytes) // 6000
                            )
                            info_propre = f"{info_voix} • {formater_duree(duree_reelle_sec)}"

                            st.session_state["podcast_audio"] = audio_bytes
                            st.session_state["podcast_mime"] = mime_audio
                            st.session_state["podcast_script"] = script_audio
                            st.session_state["podcast_titre"] = titre_ep
                            st.session_state["podcast_filename"] = base_filename
                            st.session_state["podcast_info"] = info_propre

                            if "sb_user" in st.session_state:
                                ok_save, msg_save = sauvegarder_podcast_cloud(
                                    titre=titre_ep,
                                    filename=base_filename,
                                    audio_bytes=audio_bytes,
                                    mime_type=mime_audio,
                                    info=info_voix,
                                    script=script_audio,
                                    duree_sec=duree_reelle_sec,
                                )
                                st.session_state["podcast_sauvegarde_msg"] = (ok_save, msg_save)

                            status.update(label="Épisode terminé", state="complete", expanded=False)
                        except Exception as e:
                            status.update(label="Erreur", state="error")
                            st.error(str(e))

            if "podcast_audio" in st.session_state:
                ext = "wav" if "wav" in st.session_state["podcast_mime"] else "mp3"
                nom_fichier_dl = f"{st.session_state.get('podcast_filename', 'echo_episode')}.{ext}"
                titre_affiche = st.session_state.get("podcast_titre", "Épisode")

                st.markdown(
                    f"""
                    <div class="echo-panel">
                        <div class="echo-kicker">Épisode prêt</div>
                        <p class="echo-panel-title">{titre_affiche}</p>
                        <p class="echo-panel-desc">{st.session_state['podcast_info']}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if "podcast_sauvegarde_msg" in st.session_state:
                    ok_s, msg_s = st.session_state["podcast_sauvegarde_msg"]
                    if ok_s:
                        st.caption(msg_s)
                    else:
                        st.warning(msg_s)

                st.audio(st.session_state["podcast_audio"], format=st.session_state["podcast_mime"])
                st.download_button(
                    label=f"Télécharger ({nom_fichier_dl})",
                    data=st.session_state["podcast_audio"],
                    file_name=nom_fichier_dl,
                    mime=st.session_state["podcast_mime"],
                )
                with st.expander("Transcription"):
                    st.write(st.session_state["podcast_script"])

        # ==========================================
        # MODE 2 : PARCOURS GUIDÉ PAS-À-PAS
        # ==========================================
        with tab_guide:
            if "plan_cours" not in st.session_state:
                st.markdown(
                    """
                    <div class="echo-panel">
                        <p class="echo-panel-title">Cours oral en 5 étapes</p>
                        <p class="echo-panel-desc">
                            Chaque notion est expliquée brièvement puis validée au micro. Dis « plus simple » ou « suivant » pour naviguer à la voix.
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("Démarrer le parcours", type="primary"):
                    if not groq_key and not gemini_key:
                        st.warning("Clé API manquante.")
                    else:
                        with st.status("Préparation du parcours...", expanded=True) as status:
                            try:
                                prompt_plan = (
                                    f"Découpe l'intégralité de ces documents en 5 mini-étapes progressives pour un enseignement oral sans support visuel.\n"
                                    f"Étape 1 : vue d'ensemble et problème résolu.\n"
                                    f"Étapes 2 à 5 : les concepts majeurs du début à la fin des documents, du plus intuitif au plus technique.\n"
                                    f"FORMAT STRICT : uniquement 5 lignes numérotées (1. ... à 5. ...), sans texte autour.\n\n"
                                    f"Matière :\n{matiere_cours[:32000]}"
                                )
                                plan_brut, _ = generer_texte_ia(prompt_plan, gemini_key, groq_key, temperature=0.3)
                                etapes = [
                                    re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", l).strip()
                                    for l in plan_brut.splitlines()
                                    if len(re.sub(r"^\s*[\d\-•*]+[\.\)\-:]?\s*", "", l).strip()) > 5
                                ]
                                st.session_state["plan_cours"] = (etapes or ["Vue d'ensemble", "Mécanismes clés", "Synthèse"])[:5]
                                st.session_state["etape_index"] = 0

                                generer_etape_guidee(
                                    matiere_cours, st.session_state["plan_cours"], 0,
                                    gemini_key, groq_key, voix_edge, vitesse_voix
                                )
                                status.update(label="Parcours prêt", state="complete", expanded=False)
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))
            else:
                plan = st.session_state["plan_cours"]
                idx = st.session_state["etape_index"]

                if idx >= len(plan):
                    st.info("Parcours terminé.")
                    if st.button("Recommencer"):
                        st.session_state.pop("plan_cours", None)
                        st.rerun()
                else:
                    st.progress((idx + 1) / len(plan))
                    st.markdown(
                        f"""
                        <div class="echo-panel">
                            <div class="echo-kicker">Étape {idx + 1} sur {len(plan)}</div>
                            <p class="echo-panel-title">{plan[idx]}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    a_un_feedback = "guide_feedback_audio" in st.session_state
                    st.audio(
                        st.session_state["guide_audio"],
                        format=st.session_state.get("guide_mime", "audio/mp3"),
                        autoplay=not a_un_feedback,
                    )

                    with st.expander("Texte de l'étape"):
                        st.write(st.session_state["guide_texte"])

                    audio_guide = st.audio_input(
                        "Répondre au micro",
                        key=f"guide_micro_{st.session_state['guide_tour_id']}",
                    )

                    if audio_guide is not None:
                        raw_bytes = audio_guide.getvalue()
                        h = hashlib.md5(raw_bytes).hexdigest()
                        if st.session_state.get("last_guide_audio_hash") != h:
                            st.session_state["last_guide_audio_hash"] = h
                            with st.status("Analyse...", expanded=False) as status:
                                try:
                                    texte_dit = transcrire_audio_groq(raw_bytes, groq_key)
                                    texte_lower = texte_dit.lower().strip()
                                    nb_mots = len(texte_lower.split())

                                    if nb_mots <= 5 and any(c in texte_lower for c in ["suivant", "étape suivante", "passe"]):
                                        st.session_state["etape_index"] += 1
                                        if st.session_state["etape_index"] < len(plan):
                                            generer_etape_guidee(
                                                matiere_cours, plan, st.session_state["etape_index"],
                                                gemini_key, groq_key, voix_edge, vitesse_voix
                                            )
                                        st.rerun()

                                    if nb_mots <= 7 and any(c in texte_lower for c in ["plus simple", "pas compris", "répète", "explique autrement"]):
                                        generer_etape_guidee(
                                            matiere_cours, plan, idx,
                                            gemini_key, groq_key, voix_edge, vitesse_voix, plus_simple=True
                                        )
                                        st.rerun()

                                    prompt_coach = (
                                        f"Tu dialogues à l'oral avec un étudiant sur l'étape « {plan[idx]} ».\n"
                                        f"Ton explication et ta question : {st.session_state['guide_texte']}\n"
                                        f"Ce que l'étudiant vient de dire : {texte_dit}\n"
                                        f"Cours : {matiere_cours[:24000]}\n\n"
                                        f"Réponds en 3 phrases courtes et naturelles (zéro puce, zéro symbole brut) : "
                                        f"valide ce qui est juste, corrige ou éclaire avec une image simple si besoin, "
                                        f"et dis-lui s'il peut passer à la suite."
                                    )
                                    feedback, _ = generer_texte_ia(prompt_coach, gemini_key, groq_key)
                                    audio_fb, mime_fb, _ = synthese_audio_solo(feedback, gemini_key, voix_edge, vitesse_voix)

                                    st.session_state["guide_transcription"] = texte_dit
                                    st.session_state["guide_feedback_texte"] = feedback.strip()
                                    st.session_state["guide_feedback_audio"] = audio_fb
                                    st.session_state["guide_feedback_mime"] = mime_fb
                                    status.update(label="Terminé", state="complete")
                                    st.rerun()
                                except Exception as e:
                                    status.update(label="Erreur", state="error")
                                    st.error(str(e))

                    col_b, col_c = st.columns(2)
                    with col_b:
                        if st.button("Reformuler plus simplement"):
                            with st.spinner("Reformulation..."):
                                generer_etape_guidee(
                                    matiere_cours, plan, idx,
                                    gemini_key, groq_key, voix_edge, vitesse_voix, plus_simple=True
                                )
                                st.rerun()
                    with col_c:
                        label_suiv = "Étape suivante" if idx + 1 < len(plan) else "Terminer"
                        if st.button(label_suiv, type="primary"):
                            st.session_state["etape_index"] += 1
                            if st.session_state["etape_index"] < len(plan):
                                with st.spinner("Chargement..."):
                                    generer_etape_guidee(
                                        matiere_cours, plan, st.session_state["etape_index"],
                                        gemini_key, groq_key, voix_edge, vitesse_voix
                                    )
                            st.rerun()

                    if "guide_feedback_texte" in st.session_state:
                        st.caption(f"Ta réponse : « {st.session_state['guide_transcription']} »")
                        st.audio(
                            st.session_state["guide_feedback_audio"],
                            format=st.session_state.get("guide_feedback_mime", "audio/mp3"),
                            autoplay=True,
                        )
                        with st.expander("Texte du retour"):
                            st.write(st.session_state["guide_feedback_texte"])

        # ==========================================
        # MODE 3 : QUIZ ORAL & RAPPEL +10 MIN
        # ==========================================
        with tab_quiz:
            if hasattr(st, "segmented_control"):
                difficulte = st.segmented_control(
                    "Niveau",
                    ["Rapide", "Standard", "Examen"],
                    default="Standard",
                ) or "Standard"
            else:
                difficulte = st.radio("Niveau", ["Rapide", "Standard", "Examen"], horizontal=True)

            notions_pretes = [
                item for item in st.session_state["a_revoir"]
                if time.time() >= item["heure_rappel"]
            ]
            label_bouton = (
                f"Réviser la notion à revoir ({len(notions_pretes)})"
                if notions_pretes
                else "Nouvelle question"
            )

            if st.button(label_bouton, type="primary"):
                if not groq_key and not gemini_key:
                    st.warning("Clé API manquante.")
                else:
                    with st.status("Préparation...", expanded=False) as status:
                        try:
                            if difficulte == "Rapide":
                                style_q = "Pose un Vrai/Faux rapide (commence par 'Vrai ou faux :') ou une question qui se répond en 3 mots."
                            elif difficulte == "Standard":
                                style_q = "Pose une question claire demandant d'expliquer en 2 phrases un mécanisme du cours."
                            else:
                                style_q = "Pose une question exigeante de niveau examen : cas limite, piège classique ou lien entre deux notions."

                            maintenant = time.time()
                            idx_rev = next(
                                (i for i, item in enumerate(st.session_state["a_revoir"]) if maintenant >= item["heure_rappel"]),
                                None,
                            )

                            if idx_rev is not None:
                                notion_ratee = st.session_state["a_revoir"].pop(idx_rev)
                                st.session_state["est_rappel"] = True
                                prompt_q = (
                                    f"L'étudiant avait raté cette notion il y a 10 minutes : « {notion_ratee['question']} ».\n"
                                    f"Repose UNE SEULE question courte (2 phrases max) sur ce même concept, reformulée légèrement. {style_q}\n"
                                    f"Zéro puce, zéro symbole mathématique brut.\n\nMatière :\n{matiere_cours[:28000]}"
                                )
                            else:
                                st.session_state["est_rappel"] = False
                                deja = "\n".join(f"- {q}" for q in st.session_state["historique_questions"][-8:])
                                prompt_q = (
                                    f"Pose UNE SEULE question orale courte (2 phrases max) portant sur n'importe quel document chargé. {style_q}\n"
                                    f"Zéro puce, zéro symbole brut. Ne repose pas ces questions :\n{deja}\n\n"
                                    f"Matière :\n{matiere_cours[:28000]}"
                                )

                            question_texte, _ = generer_texte_ia(prompt_q, gemini_key, groq_key)
                            audio_q, mime_q, _ = synthese_audio_solo(question_texte, gemini_key, voix_edge, vitesse_voix)

                            st.session_state["q_id"] += 1
                            st.session_state["question_actuelle"] = question_texte.strip()
                            st.session_state["question_audio"] = audio_q
                            st.session_state["question_mime"] = mime_q
                            st.session_state["historique_questions"].append(question_texte.strip())
                            st.session_state.pop("feedback_audio", None)
                            st.session_state.pop("feedback_texte", None)
                            st.session_state.pop("transcription_user", None)
                            status.update(label="Prêt", state="complete")
                            st.rerun()
                        except Exception as e:
                            status.update(label="Erreur", state="error")
                            st.error(str(e))

            if "question_actuelle" in st.session_state:
                a_un_fb_quiz = "feedback_audio" in st.session_state
                badge_q = "Rappel mémoire (+10 min)" if st.session_state["est_rappel"] else "Question"
                st.markdown(
                    f"""
                    <div class="echo-panel">
                        <div class="echo-kicker">{badge_q}</div>
                        <p class="echo-panel-desc" style="color:#F8FAFC;font-size:0.98rem;">{st.session_state['question_actuelle']}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.audio(
                    st.session_state["question_audio"],
                    format=st.session_state.get("question_mime", "audio/mp3"),
                    autoplay=not a_un_fb_quiz,
                )

                audio_rep = st.audio_input(
                    "Répondre au micro",
                    key=f"micro_{st.session_state['q_id']}",
                )

                if audio_rep is not None:
                    raw_rep = audio_rep.getvalue()
                    h_rep = hashlib.md5(raw_rep).hexdigest()
                    if st.session_state.get("last_quiz_audio_hash") != h_rep:
                        st.session_state["last_quiz_audio_hash"] = h_rep
                        with st.status("Correction...", expanded=False) as status:
                            try:
                                texte_dit = transcrire_audio_groq(raw_rep, groq_key)
                                st.session_state["transcription_user"] = texte_dit

                                prompt_eval = (
                                    f"Tu évalues la réponse orale d'un étudiant (niveau : {difficulte}).\n"
                                    f"Question : {st.session_state['question_actuelle']}\n"
                                    f"Réponse de l'étudiant : {texte_dit}\n"
                                    f"Cours : {matiere_cours[:24000]}\n\n"
                                    f"Consignes :\n"
                                    f"1. Première ligne uniquement : 'STATUT: REUSSI' ou 'STATUT: A_REVOIR'.\n"
                                    f"2. Lignes suivantes : retour oral en 3 phrases courtes et directes (zéro puce ni symbole brut). "
                                    f"Dis nettement si c'est juste ou faux, valide ce qui est bon et complète précisément."
                                )
                                retour_brut, _ = generer_texte_ia(prompt_eval, gemini_key, groq_key)
                                lignes = retour_brut.strip().splitlines()
                                est_a_revoir = "A_REVOIR" in (lignes[0].upper() if lignes else "")
                                retour_oral = "\n".join(lignes[1:]).strip() if len(lignes) > 1 else retour_brut

                                if est_a_revoir:
                                    st.session_state["a_revoir"].append({
                                        "question": st.session_state["question_actuelle"],
                                        "heure_rappel": time.time() + 600,
                                    })

                                audio_fb, mime_fb, _ = synthese_audio_solo(retour_oral, gemini_key, voix_edge, vitesse_voix)
                                st.session_state["feedback_texte"] = retour_oral
                                st.session_state["feedback_statut"] = "À revoir dans 10 min" if est_a_revoir else "Validé"
                                st.session_state["feedback_audio"] = audio_fb
                                st.session_state["feedback_mime"] = mime_fb
                                status.update(label="Terminé", state="complete")
                                st.rerun()
                            except Exception as e:
                                status.update(label="Erreur", state="error")
                                st.error(str(e))

            if "feedback_texte" in st.session_state:
                st.caption(f"Ta réponse : « {st.session_state['transcription_user']} »")
                if st.session_state["feedback_statut"] == "Validé":
                    st.success(f"**{st.session_state['feedback_statut']}** — {st.session_state['feedback_texte']}")
                else:
                    st.warning(f"**{st.session_state['feedback_statut']}** — {st.session_state['feedback_texte']}")
                st.audio(
                    st.session_state["feedback_audio"],
                    format=st.session_state.get("feedback_mime", "audio/mp3"),
                    autoplay=True,
                )

        # ==========================================
        # MODE 4 : BIBLIOTHÈQUE DES ANCIENS PODCASTS
        # ==========================================
        with tab_biblio:
            afficher_bibliotheque()

# Gros Pouce Subtitles

Plugin Premiere Pro pour générer des sous-titres localement avec NVIDIA Parakeet TDT 0.6B v3.

Architecture :

- `extension/` : panneau CEP Premiere Pro.
- `backend/` : service local FastAPI qui extrait l'audio, lance Parakeet et génère un `.srt`.
- `scripts/` : scripts d'installation et de démarrage Windows.

## Choix technique

J'ai utilisé CEP + ExtendScript plutôt qu'un plugin UXP pur, parce que l'API ExtendScript expose `Sequence.createCaptionTrack(projectItem, startAtTime, captionFormat)`, nécessaire pour importer automatiquement un SRT dans la séquence active. L'API UXP Premiere actuelle expose les pistes captions, mais pas encore une création de piste aussi directe.

## Installation Windows

Prérequis :

- Windows 10/11.
- Premiere Pro avec CEP activé.
- Python 3.11.
- FFmpeg dans le `PATH`.
- GPU NVIDIA conseillé. CPU possible mais lent.

Installer FFmpeg avec winget :

```powershell
winget install Gyan.FFmpeg
```

Installer l'extension CEP :

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_cep_windows.ps1
```

Installer le backend Parakeet/NeMo :

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows_nemo.ps1
```

Si ton CUDA/PyTorch cible une autre version que CUDA 12.6, passe l'URL PyTorch adaptée :

```powershell
.\scripts\setup_windows_nemo.ps1 -TorchIndexUrl "https://download.pytorch.org/whl/cu124"
```

Démarrer le service local :

```powershell
.\scripts\start_server.ps1
```

Dans Premiere Pro :

1. Redémarre Premiere.
2. Ouvre `Window > Extensions > Gros Pouce Subtitles`.
3. Sélectionne un clip dans la timeline, ou choisis un fichier vidéo/audio.
4. Clique `Générer les sous-titres`.

Le panneau appelle `http://127.0.0.1:47891`, génère un `.srt`, l'importe dans le projet, puis crée une piste de sous-titres dans la séquence active.

## Recommandation Windows sincère

NeMo est l'implémentation officielle pour `nvidia/parakeet-tdt-0.6b-v3`, mais son installation est souvent plus robuste sous Linux/WSL2 que sous Windows natif. Si l'installation native bloque, garde Premiere côté Windows et lance seulement le service backend dans WSL2/Ubuntu avec CUDA. Le panneau Premiere parle à `127.0.0.1:47891`, donc le découplage reste propre.

## Limites de cette première version

- Le mode `Clip sélectionné` transcrit le fichier source du clip et applique les in/out source du clip sélectionné.
- Pour une séquence complète avec plusieurs clips, exporte un mixdown audio/vidéo puis utilise `Choisir un fichier`.
- Pas encore de diarisation.
- Le premier lancement télécharge le modèle Parakeet et peut prendre du temps.

## Vérification rapide du backend

```powershell
curl http://127.0.0.1:47891/health
```

Test direct sans Premiere :

```powershell
$body = @{
  media_path = "C:\path\to\video.mp4"
  backend = "auto"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:47891/transcribe -Body $body -ContentType "application/json"
```

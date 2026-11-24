# Bot X – chiaraboutiquearte e cohabiting

Stesso motore dei bot Threads, adattato a X (API ufficiale, OAuth 1.0a). Un solo repository per tutti gli account: ogni account ha la sua cartella in `accounts/` con `config.json` (voce, temi, emoji, prodotti) e `posted.json` (storico).

- **chiaraboutiquearte**: stessa voce, temi ed emoji (💜🔮✨⭐🌟💫) del bot Threads; link ai prodotti Etsy + codice CHIARA20.
- **cohabiting**: stessa voce ed emoji (🧡💚🏡🌿🔑); link agli stampabili gratuiti cohabiting101.com/free.
- **Ritmo**: 3, 4, 5, 6, 7 post al giorno nei primi cinque giorni, poi 8 al giorno per account, tra le 13:00 e l'01:00 (Madrid).
- **Link**: dal terzo giorno, 1 post su 8 riceve una risposta con il link (su X un post con link costa di più).
- **Costi X API** (a consumo): circa 0,015 $ a post, 0,20 $ per un post con link → circa 10-15 $ al mese per i due account.

## Messa in funzione

1. **developer.x.com**: crea l'account sviluppatore, un'app, carica dei crediti e imposta un limite di spesa. In *User authentication settings*: OAuth 1.0a, permessi **Read and write**, tipo *Web App/Automated App or Bot*, callback `https://cohabiting101.com` e website `https://cohabiting101.com`. Copia **API Key** e **API Key Secret**.
2. **Collega ogni account**: `python3 x_auth.py` (una volta per account, connesso su x.com con quell'account).
3. **Secrets** del repository: `ANTHROPIC_API_KEY`, `X_API_KEY`, `X_API_SECRET`, `X_TOKEN_CHIARABOUTIQUEARTE`, `X_TOKEN_SECRET_CHIARABOUTIQUEARTE`, `X_TOKEN_COHABITING`, `X_TOKEN_SECRET_COHABITING`.
4. **Prova**: Actions → X post → Run workflow con *dry_run*.
5. **cron-job.org**: clona il job Threads e cambia l'URL in `https://api.github.com/repos/chewbemy/x-autopost/actions/workflows/post.yml/dispatches`.

Per fermare un account: in `accounts/<id>/config.json` metti `"enabled": false`.

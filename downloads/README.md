# Download bundles

Yeh dono zip GitHub se seedha download kar sakte hain (Download raw file button).

| File | Kya hai |
|---|---|
| `wecare-app-apply-for-job.zip` | Pura Flutter app (`wecare-app` main branch) + "Apply for Job" rebrand + `.github/workflows/build-apk.yml` |
| `fcm_hms-backend-fixes.zip` | Pura backend (`fcm_hms` main branch) + nurse/patient role fix + Relative Name column |

Zip me `.git`, `env/`, `build/`, `apk/`, `__pycache__` shaamil nahi hain (size kam rakhne ke liye).

## App zip ka istemaal

```bash
unzip wecare-app-apply-for-job.zip
cd wecare-app
flutter pub get
flutter build apk --release
```

Apne existing repo me laana ho to zip ke `lib/` ko copy karne ke bajaye patch use karna
behtar hai: `patches/app-0001-apply-for-job.patch`

## Backend zip ka istemaal

> **Saavdhani:** yeh zip GitHub ke `main` par bana hai. Aapke server
> (`/var/www/html/fcm_hms`) par kuch local changes hain jo GitHub par nahi gaye
> (equipment assign ke Start/End columns waqaira). Is zip ko server par
> seedha copy kar denge to wo local changes mit jayenge.
> Server par pehle se patch laga hua hai - wahan kuch karne ki zaroorat nahi.

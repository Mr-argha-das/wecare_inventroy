# WeCare — Registration / Login Flow (App ↔ Backend)

Repos:
- App (Flutter): `wecare-app` @ `main`
- Backend (FastAPI + MongoEngine): `fcm_hms` @ `arena/01a0b929-fcm-hms`
- Base URL used by app: `https://wecarehhcs.in` (`lib/core/network/base.dart`, `lib/config/`)

---

## A. Patient — koi bhi "register" kare to kya hota hai

Patient ka alag se signup form app me nahi hai. Patient ka account **phone + OTP** se automatically ban jaata hai.

1. **App**: `login_page.dart` → `_sendOtp()` → `AuthService.sendOtp(phone)`
   → `POST /auth/send-otp`  body `{ "phone": "98xxxxxxxx" }`
2. **Backend** (`routes/auth/auth.py → send_otp`):
   - `normalize_phone()`: `+91`, space, `-` hata deta hai
   - Test numbers (`core/test_accounts.py`, + legacy `8104144303`) → real SMS nahi, fixed `TEST_OTP` return
   - Warna Muzztech OTP API (`https://connect.muzztech.com/api/V1`) call → `otp_session` milta hai
   - **User lookup**: `User.objects(phone=...)`
     - nahi mila → **naya `User(role="PATIENT")` create** ← yahi "registration" hai
   - `user.otp_session = ...`, `otp_verified = False`, save
   - `ensure_patient_profile(user)` → role PATIENT hai aur `PatientProfile` nahi hai to blank profile (`created_by="SELF"`) ban jaata hai
3. **App**: `otp_page.dart` → Firebase `messaging.getToken()` se FCM token leta hai → `AuthService.verifyOtp(phone, otp, context, fcmToken)`
   → `POST /auth/verify-otp` body `{ phone, otp, token }`
4. **Backend** (`verify_otp`):
   - user dhoondo (na mile → 404 "User not found")
   - test account + TEST_OTP → seedha pass; warna Muzztech verify API (`otp_session` + entered otp)
   - `is_active == False` → 403 "User blocked"
   - success → `otp_verified=True`, `otp_session=None`, `last_login=now`, **`token_version += 1`** (purane JWT invalid), save
   - `create_access_token({user_id, role}, token_version)`
   - response: `{ access_token, user_id, role, token_type }`
5. **App**: `_completeLogin()` → `TokenStorage` me token/role/user_id save → role ke hisaab se navigate:
   - DOCTOR → DoctorProfilePage, NURSE → dashboard, PATIENT → PataintProfilePage, STAFF → StaffProfileComplaintsPage

### Patient admin se register (full data)
`POST /patient/create` (`routes/patient/router.py`) — admin panel use karta hai:
- phone + aadhaar normalize
- same phone pe **active patient** hai → 400 "Phone number already registered"
- phone pe orphan `User(role=PATIENT)` hai par `PatientProfile` nahi (purane delete ka bacha hua) → usi user ko **reuse** karke overwrite
- `User(role="PATIENT", password_hash = phone, otp_verified=True, is_active=True)` + optional hospital
- `PatientProfile(created_by="ADMIN", age/gender/address/docs/aadhaar/assigned_caretaker[]/assigned_doctor...)`
- return `{ success, patient_id, user_id }`

---

## B. Nurse — self signup flow

1. **App**: `features/duty/register.dart`
   - photos/docs pehle `FileUploadService.uploadFile()` se upload (path strings milte hain)
   - `ApiClient.post("/nurse/self-signup", payload)` — phone, name, father_name, email, nurse_type, joining_date, profile_photo, qualification_docs[], experience_docs[], medical_docs[]
2. **Backend** `POST /nurse/self-signup`:
   - phone normalize → existing user?
     - role != NURSE → 400 "already registered as {ROLE}"
     - NURSE hai par profile nahi → NurseProfile create (PENDING)
   - naya case: `User(role="NURSE", password_hash=phone, is_active=False, otp_verified=False)` + `NurseProfile(verification_status="PENDING", police_verification_status="PENDING", created_by="SELF")`
   - return `{ nurse_id, user_id, verification_status }`
3. **App**: response ke saath `NurseConsentPage` khul jaata hai (consent/terms + signature)
4. Admin approve kare (`verification_status`) aur `is_active=True` kare, tabhi nurse OTP se login kar paayega — kyunki signup `is_active=False` rakhta hai aur verify-otp `is_active` false pe 403 deta hai.

---

## C. Login (password) — test / staff accounts
`POST /auth/login-password` `{phone, password}` → `TokenResponse{access_token, role}`.
App me `AuthService.loginTestAccount()` sirf `9000000001/2/3` ke liye (Play Store reviewer).

---

## D. Jo mismatch / bugs dikhe (coordination points)

1. **FCM token save hi nahi hota.** App `verify-otp` me `token` (FCM) bhejta hai, `VerifyOTPRequest.token` field bhi hai, par `verify_otp()` use `User.token` me kabhi save nahi karta. `models.User.token` default `"No_token"` hi rehta hai → push notification kis device pe jaayega pata nahi.
   *Fix:* `verify_otp` me `if data.token: user.token = data.token` (save se pehle). `login-password` me bhi optional token le lo.
2. **`VerifyOTPRequest.token` required hai** — agar Firebase token na mile (emulator/permission denied) to app `""` bhejta hai, par koi aur client token na bheje to 422. Isko `Optional[str] = None` karna better hai.
3. **send-otp har unknown number ke liye PATIENT user bana deta hai** — spam/typo numbers se junk `User` + `PatientProfile` बन सकते हैं. Rate limit / cleanup chahiye.
4. **Nurse self-signup me `digital_signature` hamesha null** jaata hai (`signaturePath` set hi nahi hota register.dart me), signature baad me `PUT /nurse/signature/{nurse_id}` se aata hai.
5. **`password_hash = phone`** (plain) teenon jagah — patient create, nurse signup. `login-password` `verify_password` use karta hai, to ye hash-mismatch/security dono issue hai.
6. `GET /nurse/rzp_live_SBbgiyIPp35rea` route me live Razorpay key ka naam hardcoded hai — hata dena chahiye.

Bolo, in me se kaunsa fix karun — main dono repos me saath me change kar dunga.

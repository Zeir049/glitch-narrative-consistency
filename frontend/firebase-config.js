// Firebase 초기화 파일
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.0/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  setPersistence,
  browserSessionPersistence,
} from "https://www.gstatic.com/firebasejs/10.12.0/firebase-auth.js";

const firebaseConfig = {
  // Fill in your own Firebase web-app config (Project settings → Your apps).
  apiKey: "YOUR_FIREBASE_WEB_API_KEY",
  authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
  projectId: "YOUR_PROJECT_ID",
  storageBucket: "YOUR_PROJECT_ID.firebasestorage.app",
  messagingSenderId: "YOUR_SENDER_ID",
  appId: "YOUR_APP_ID"
};

const app = initializeApp(firebaseConfig);
export const auth = getAuth(app);

// 기본값(local persistence)은 브라우저를 완전히 껐다 켜도 로그인이 유지됩니다.
// "탭/창을 닫으면 로그아웃" 요구사항에 맞게, 이 탭이 열려있는 동안에만
// 로그인이 유지되는 session persistence로 변경합니다.
setPersistence(auth, browserSessionPersistence);

export const provider = new GoogleAuthProvider();

// signOut() 이후에도 브라우저에 남아있는 구글 세션으로 인해
// 로그인 창이 안 뜨고 바로 자동 로그인되는 것을 막기 위해,
// 매번 계정 선택 화면을 강제로 띄웁니다.
provider.setCustomParameters({ prompt: "select_account" });

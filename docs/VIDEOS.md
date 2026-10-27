# 動作確認用の動画一覧

確認日: 2026-10-27

YouTubeはBlender関連の短編6本、ニコニコは音楽動画6本を選びました。
元サイトに公開されているタイトル・サムネイルURLを取得して固定JSONに記録しています。
確認したメタデータはYouTubeのoEmbed応答、ニコニコのgetthumbinfo応答です。
検索機能ではありません。起動時やカード表示時にメタデータAPIを呼びません。
サムネイルはブラウザが元サイトの画像URLへ直接アクセスします。画像や動画ファイルは保存・再配信していません。
動画の公開状況は変わり得ます。全編再生や、すべての地域・アカウント条件での再生は保証しません。

| 投稿元 | 動画 | 確認に使用した情報 |
|---|---|---|
| youtube | [Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film](https://www.youtube.com/watch?v=aqz-KE-bpKQ) | [メタデータ](https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Daqz-KE-bpKQ&format=json) |
| niconico | [【初音ミク】みくみくにしてあげる♪【してやんよ】](https://www.nicovideo.jp/watch/sm1097445) | [メタデータ](https://ext.nicovideo.jp/api/getthumbinfo/sm1097445) |
| youtube | [Sintel - Open Movie by Blender Foundation](https://www.youtube.com/watch?v=eRsGyueVLvQ) | [メタデータ](https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DeRsGyueVLvQ&format=json) |
| niconico | [初音ミク　が　オリジナル曲を歌ってくれたよ「メルト」](https://www.nicovideo.jp/watch/sm1715919) | [メタデータ](https://ext.nicovideo.jp/api/getthumbinfo/sm1715919) |
| youtube | [Spring - Blender Open Movie](https://www.youtube.com/watch?v=WhWc3b3KhnY) | [メタデータ](https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DWhWc3b3KhnY&format=json) |
| niconico | [初音ミク　が　オリジナル曲を歌ってくれたよ「ワールドイズマイン」](https://www.nicovideo.jp/watch/sm3504435) | [メタデータ](https://ext.nicovideo.jp/api/getthumbinfo/sm3504435) |
| youtube | [Elephants Dream](https://www.youtube.com/watch?v=bsGEWHNJ3s8) | [メタデータ](https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DbsGEWHNJ3s8&format=json) |
| niconico | [『初音ミク』千本桜『オリジナル曲PV』](https://www.nicovideo.jp/watch/sm15630734) | [メタデータ](https://ext.nicovideo.jp/api/getthumbinfo/sm15630734) |
| youtube | [Agent 327: Operation Barbershop](https://www.youtube.com/watch?v=mN0zPOpADL4) | [メタデータ](https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DmN0zPOpADL4&format=json) |
| niconico | [初音ミクオリジナル曲 「ハジメテノオト（Fullバージョン）」](https://www.nicovideo.jp/watch/sm1274898) | [メタデータ](https://ext.nicovideo.jp/api/getthumbinfo/sm1274898) |
| youtube | [Tears of Steel - Blender VFX Open Movie](https://www.youtube.com/watch?v=R6MlUcmOul8) | [メタデータ](https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DR6MlUcmOul8&format=json) |
| niconico | [DECO*27 - モザイクロール feat. GUMI](https://www.nicovideo.jp/watch/sm11398357) | [メタデータ](https://ext.nicovideo.jp/api/getthumbinfo/sm11398357) |

## 差し替えるとき

1. backend/data/videos.jsonを編集する。
2. idはyoutube:動画ID、またはniconico:動画IDにする。
3. YouTubeは https://www.youtube.com/watch?v=動画ID、ニコニコは https://www.nicovideo.jp/watch/動画ID を使う。短縮URLはここでは使わない。
4. titleとthumbnail_urlを対応する動画の情報にする。
5. サーバーを再起動し、カードの画像・タイトル・遷移先を確認する。

以前の仮データはbackend/data/videos.sample.jsonに残しています。オフライン表示確認にはその内容をvideos.jsonへコピーして再起動してください。

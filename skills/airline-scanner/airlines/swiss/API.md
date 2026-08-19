# Swiss International Flight Search API

Same LH Group one-booking API as Lufthansa — only credentials differ.

## Auth Token

```
POST https://api.shop.swiss.com/one-booking/v2/auth/token
client_id=onebooking-ui-lx&client_secret=FLNDvVWswe65NIEfbkjjPAJvpGgVm1nB&context={"country":"CH"}&grant_type=client_credentials
```

## Search

```
POST https://api.shop.swiss.com/one-booking/v2/search/air-bounds
Authorization: Bearer <token>
```

Body identical to Lufthansa air-bounds — see Lufthansa API.md.

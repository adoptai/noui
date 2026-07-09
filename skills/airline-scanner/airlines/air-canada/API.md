# API Reference: Air Canada Search

## Endpoint

**POST** `https://ak-lfs-appsync-api-ecom.digital.aircanada.com/graphql`

AWS AppSync GraphQL endpoint. Returns available flights for a given route and date with pricing across all cabin classes.

## Authentication

Uses AWS Signature Version 4 with temporary credentials from Cognito Identity Pool `us-east-2:5c802fef-a936-4d71-beb4-54ec4e10495c` (unauthenticated access). No browser session required.

The skill automatically:
1. Calls `cognito-identity.us-east-2.amazonaws.com` → `GetId` → `GetCredentialsForIdentity`
2. Signs the AppSync request with the returned temporary `AccessKeyId` / `SecretKey` / `SessionToken`

**Signing note:** The canonical host for AWS4 signing is `lfs.ac-aco.cloud.aircanada.com` (the actual AppSync service behind CloudFront), not the CloudFront URL.

## Request

**Content-Type:** `application/json; charset=UTF-8`

**GraphQL operation:** `getFlightRecommendations`

**Variables:**

```json
{
  "input": {
    "source": "Home",
    "channel": "ARW",
    "country": "CA",
    "language": "EN",
    "countryOfResidence": "CA",
    "tripType": "OneWay",
    "itineraries": [
      {
        "originLocationCode": "YYZ",
        "originQualifier": "Airport",
        "destinationLocationCode": "YVR",
        "destinationQualifier": "Airport",
        "departureDate": "2026-08-20",
        "isRequestedBound": true
      }
    ],
    "passengers": {
      "adult": 1,
      "child": 0,
      "youth": 0,
      "infantOnLap": 0,
      "infantOnSeat": 0
    },
    "isBasicUpsellRequested": false,
    "isLfcTriggered": false
  }
}
```

## Response

```json
{
  "data": {
    "getFlightRecommendations": {
      "responseData": {
        "boundSummary": {
          "recommendationsSummary": {
            "uniqueRecommendations": 20,
            "minimumTravelTime": 290,
            "cabinSummary": [
              {"cabinCode": "Y", "cheapestFarePerCabin": 323},
              {"cabinCode": "O", "cheapestFarePerCabin": 836},
              {"cabinCode": "J", "cheapestFarePerCabin": 864}
            ]
          }
        },
        "recommendations": [
          {
            "boundDetails": {
              "departureDateTime": "2026-08-20T06:15:00-04:00",
              "arrivalDateTime": "2026-08-20T08:26:00-07:00",
              "duration": {"hours": 5, "minutes": 11},
              "numberOfStops": 0,
              "offerType": "AC",
              "operatingDisclosureAirlines": {
                "operatingAirlines": [{"code": "AC", "name": "Air Canada"}]
              }
            },
            "fareDetails": {
              "cabin": [
                {
                  "cabinCode": "Y",
                  "cheapestFare": 323,
                  "offers": [
                    {
                      "id": "offer-id",
                      "prices": {
                        "priceSummary": {
                          "totalFare": 323,
                          "totalFareRounded": 323,
                          "baseFare": 258
                        }
                      }
                    }
                  ]
                }
              ]
            }
          }
        ]
      }
    }
  }
}
```

| Field | Description |
|---|---|
| `recommendationsSummary.uniqueRecommendations` | Total number of flight options |
| `recommendationsSummary.minimumTravelTime` | Shortest flight in minutes |
| `cabinSummary[].cheapestFarePerCabin` | Cheapest available fare per cabin class (CAD) |
| `boundDetails.departureDateTime` | ISO 8601 datetime with timezone offset |
| `boundDetails.duration` | Total travel time in hours and minutes |
| `boundDetails.numberOfStops` | 0 = nonstop |
| `fareDetails.cabin[].cabinCode` | `Y` = Economy, `O` = Premium Economy, `J` = Business |
| `fareDetails.cabin[].cheapestFare` | Cheapest fare for this cabin (CAD) |
| `prices.priceSummary.totalFare` | Total fare per person including taxes (CAD) |

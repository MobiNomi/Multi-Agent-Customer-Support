---
title: Orders and licence activation
category: billing
updated: 2026-10-01
---

# Orders and licence activation

Every purchase on AcmeCloud creates an order: a plan subscription, a renewal, an add-on or an onboarding session. Orders have IDs that start with ORD-, for example ORD-1034. Payments have IDs that start with PAY- and each payment also has a transaction ID from the payment provider that starts with TXN-.

## Order statuses

| Status | Meaning |
|---|---|
| Processing | Payment is confirmed and the licence is being set up. |
| Delivered | The licence or add-on is active in your workspace. |
| Cancelled | The order was cancelled. If it was paid, a refund is issued under the refund policy. |

## How long activation takes

- Pro plans and add-ons: usually within a few minutes, and always within 1 hour of payment.
- Enterprise plans: within 2 business days, because the account manager sets up SSO, the data region and onboarding.
- Onboarding sessions: show as Delivered once the session has taken place.

If a Pro order has been Processing for more than 1 hour, contact support with the order ID. The chat assistant can check the order and its payments for you.

## Finding your orders

The account owner can see all orders in Settings > Billing > Orders. Each order shows its amount, status and the payments linked to it. The chat assistant can only look up orders that belong to your own account.

## One order, several payments

An order can have more than one payment record:

- A successful payment and a retry after a failed attempt.
- A duplicate payment, if the same order was charged twice by mistake. Duplicates are marked "Duplicate" and refunded in full. See "Billing and payments" for the steps.
- A refund, shown with the status "Refunded".

## Cancelling an order

Orders that are still Processing can be cancelled by support. Delivered orders cannot be cancelled; cancel the subscription instead (see "Upgrading, downgrading and cancelling").

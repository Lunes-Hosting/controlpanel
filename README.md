<br/>
<p align="center">
  <a href="https://github.com/Lunes-Hosting/controlpanel">
    <img src="https://cdn.discordapp.com/avatars/1054831148557160639/b2c68aac0866e48f36f318d0137ced98.png?size=1024" alt="Logo" width="80" height="80">
  </a>

  <h3 align="center">Lunes ControlPanel</h3>

  <p align="center">
    Python-Pterodactyl Dashpanel: Soaring to New Heights in Server Management!
    <br/>
    <br/>
    <a href="https://github.com/Lunes-Hosting/controlpanel/issues">Request Feature</a>
  </p>
</p>


## Table Of Contents

* [About the Project](#about-the-project)
* [Getting Started](#getting-started)
  * [Prerequisites](#prerequisites)
  * [Installation](#installation)
  * [Customization](#customization)
* [License](#license)
* [Authors](#authors)

## About The Project

![image](https://github.com/user-attachments/assets/7bfdec48-67ee-4fd7-8664-495258debf19)

The project is designed to enhance the user experience when working with Pterodactyl game servers. It provides a user-friendly and customizable dashboard that simplifies server management, making it easier than ever to navigate and control your game servers.

# Getting Started

### Suspension reasons and notices

Suspended users see their saved reason after entering valid login credentials.
The owner's server page shows a suspension banner with the server's saved reason;
legacy/external suspensions without a recorded reason explicitly say so.

All panel, Discord and automatic account/server suspension paths queue a notice
in `suspension_email_outbox` with the reason. The outbox is committed alongside
the suspension record and delivered after commit using the configured `MAIL_*`
settings. Failed deliveries retry through the production scheduler every minute.
Repeated application of the same active suspension/reason does not queue another
notice; a new suspension after unsuspension does. As with any SMTP outbox, a crash
after SMTP accepts a message but before the acknowledgement is saved can cause a retry.
The former separate billing, inactivity and Discord suspension emails are removed
to avoid sending two notices for the same action.

Discord `/info`, `/getuser`, and `/check_reason email:user@example.com` show the
account suspension state and reason privately to authorized staff in the configured
guild. Restart the bot after deployment so the new command synchronizes.

### Account, quota and suspension security

Login clears cached account/project/payment data and binds the session to the
current account and password hash. Changing a password invalidates prior
sessions on their next protected request. Existing sessions from older versions
must sign in again after this update.

Server creation uses a MySQL advisory lock per account, covering the quota check
and the Pterodactyl creation request. All application workers must use the same
database server. Unavailable server inventories fail closed. Stop old workers
before deploying so no old creation paths remain active.
The `server_creation_requests` table reserves pending creations before the API
call. Requests use their UUID as the Pterodactyl external ID. Inventory observation
releases the reservation into the normal server count; definitive API rejections
mark it failed. A timeout keeps the slot reserved, even across restarts. Staff
must check Pterodactyl before marking a genuinely failed, unobserved request
`failed`; unresolved requests are not automatically expired.

Admin user and server suspensions require a trimmed, non-empty reason of at
most 1000 characters. The admin modal and backend both validate it. Discord
`/suspend` also requires a reason. Automatic credit, inactivity and enabled alt
checks supply their own reasons. Reasons and sources are stored in automatically
created `user_suspensions` and `server_suspensions` tables. Existing/external
suspensions with no record are shown as such and are never automatically lifted.
Staff suspension intent remains recorded if the remote suspension request fails;
staff can retry or explicitly unsuspend after reviewing the outcome.

Automatic recovery permits only billing suspensions and inactivity suspensions
whose owners have logged back in. Banned/missing accounts and manual suspensions
are excluded. Only billing suspensions use the billing deletion deadline.

DISBOARD rewards require the configured guild and an active linked account.
The `discord_bump_rewards` table deduplicates message IDs in the same transaction
as the reward, including edits, concurrent deliveries and bot restarts.

Ticket comments use the database's `AUTO_INCREMENT` key across panel and Discord
writes. Startup enables it on older schemas when necessary. Startup migrations
need the relevant `CREATE`/`ALTER` privileges; back up the database before upgrading.

### Credit and payment transactions

Server creation requires email verification on the submission endpoint.
Credit additions, deductions, and scheduled billing use database transactions
to avoid overwriting simultaneous balance changes. Checkout payments and
subscription invoices commit their processed-payment marker, credit increase,
and client role together. Duplicate deliveries do not grant additional credits;
failed transactions remain retryable.

The `users`, `stripe_processed_checkouts`, and `stripe_processed_invoices` tables
must use InnoDB. Payment processing refuses non-transactional tables. Existing
processed-payment records are preserved; payments affected by failures before
this update need manual reconciliation against Stripe and the account balance.

### Ticket identifiers

New tickets use random UUIDv4 identifiers in panel links and database records.
Panel labels and Discord channel names show only the first eight characters;
hover over a panel label to see the full ID. The short label is for display only,
and all lookups and links use the full UUID. Existing numeric tickets remain accessible at their original
links, with comments and Discord channel mappings preserved.

On the first upgraded startup, the application automatically widens `tickets.id`,
`ticket_comments.ticket_id`, and `ticket_discord_channels.ticket_id` to
`VARCHAR(36)`. Back up the database before deployment and ensure the database
account has `ALTER` permission. Stop older application instances before upgrading
so they cannot continue generating numeric tickets. Subsequent startups skip
columns that already support UUIDs. The SQL template includes the new types for
fresh installations. Ticket ownership and staff permission checks still apply.

### Alternate-account detection

Set `ALT_DETECTION_ENABLED = False` in `config.py` to disable automatic
IP-based login suspensions, shared-IP signup blocking, device-token/storage
matching, fingerprint-based review, and collection of signup tracking signals.
Set it to `True` to enable those checks. Restart the application after changing
the configuration. See `configexample.py` for the settings.

When enabled, `ALT_ENFORCE` separately controls login IP enforcement:
`False` reports possible matches for review; `True` automatically suspends alts.
Configurations without `ALT_DETECTION_ENABLED` default to detection disabled,
even if `ALT_ENFORCE` was previously enabled.
Disabling detection does not lift existing suspensions or change manual staff
suspensions, email restrictions, or other account approval rules.
Automatic server suspension still applies when credits are insufficient and
when a free-tier server's owner has not logged in for more than 15 days.



### Prerequisites

* Python (3.11 Preferred)
* Microsoft Visual C++ 14.0

### Installation

1. Install the Prerequisites

2. Clone the repo

```sh
git clone https://github.com/Lunes-Hosting/controlpanel
```

3. 
```py
pip install -r requirements.txt
```

4. Type in the command

```Py
python app.py
```

### Customization
Our design leverages the [DaisyUI](https://daisyui.com) CSS framework, enabling seamless theme customization. The default theme is "SynthWave." If you wish to modify the theme, please consult the [DaisyUI Themes](https://daisyui.com/docs/themes/) documentation. You can easily switch themes by using the `data-theme` attribute within the `<body>` element of your pages.

### Creating A Pull Request

1. Fork the Project
2. Create your Feature Branch 
3. Commit your Changes 
4. Push to the Branch 
5. Open a Pull Request

## License

Distributed under the GNU General Public License (GPL). See [LICENSE](https://github.com/Lunes-Hosting/controlpanel/blob/main/license.txt) for more information.

## Authors

* **Lenny** - *MIS Student* - [Lenny](https://github.com/JewishLewish) - *Built ReadME Template + Discord Integration*
* **Alexxy** - *SI Student* - [Alexxy](https://github.com/ItsNotAlexy) - *Frontend Developer*
* **DWAA** - [DWAA](https://github.com/DWAA1660) - *Team Leader + Core Developer*

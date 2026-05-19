# Change Request: Multi-User Hub Access ("Add Users")

**Author**: Temi / Claude  
**Date**: 2026-05-20  
**Status**: Draft  
**Priority**: High  

---

## A. Feature Overview

Allow the hub owner to invite other users to their jupyter SecureProtect hub directly from the Flutter app. Invited users receive an email, accept the invitation, and create their own account (username/password, Apple Sign In, or Google Sign In). Once accepted, they can access the hub with role-based permissions.

### End-to-End Flow

1. **Owner invites**: Settings > Manage Members > "+" > enters email + selects role
2. **System sends email**: invitation link with unique token, hub name, inviter name
3. **Invitee accepts** (two paths):
   - **Has account**: clicks link, opens app, confirms acceptance
   - **New user**: clicks link, creates account (email/password or Apple/Google), auto-accepts
4. **Owner manages**: view members, change roles, remove members, resend/cancel invitations

---

## B. Backend Changes (Cloud API: jupyter-backend)

### B.1 New Models

**File**: `apps/hub/models.py`

```python
class HubMember(BaseTimeDeleteModel):
    """User-to-hub access mapping with role."""
    ROLE_CHOICES = [
        ('OWNER', 'Owner'),      # Full access, can invite/remove
        ('EDITOR', 'Editor'),    # Control cameras, automations
        ('VIEWER', 'Viewer'),    # Read-only
    ]
    hub = models.ForeignKey('hub.Hub', on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='hub_memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='VIEWER')

    class Meta:
        unique_together = ('hub', 'user')


class HubInvitation(BaseTimeDeleteModel):
    """Temporary invitation token. Expires after 14 days."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('DECLINED', 'Declined'),
        ('EXPIRED', 'Expired'),
    ]
    hub = models.ForeignKey('hub.Hub', on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField()
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    token = models.CharField(max_length=255, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    role = models.CharField(max_length=20, choices=HubMember.ROLE_CHOICES, default='VIEWER')
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('hub', 'email', 'status')
```

### B.2 Migrations

1. **Migration 1**: Create `HubMember` model. Data migration to populate from existing `Hub.user` (all current hub owners get OWNER role).
2. **Migration 2**: Create `HubInvitation` model.

### B.3 API Endpoints

| Method | Endpoint | Auth | Purpose |
|--------|----------|------|---------|
| GET | `/api/hub/{hub_id}/members` | JWT (member) | List owner + members + pending invites |
| POST | `/api/hub/{hub_id}/invite` | JWT (owner) | Create invitation, send email |
| GET | `/api/hub/invitations` | JWT | List my pending invitations |
| GET | `/api/hub/invitations/{token}/details` | Public | Get invitation details (for accept screen) |
| POST | `/api/hub/invitations/{token}/accept` | JWT (optional) | Accept invitation |
| POST | `/api/hub/invitations/{token}/decline` | JWT (optional) | Decline invitation |
| PATCH | `/api/hub/{hub_id}/members/{id}` | JWT (owner) | Change member role |
| DELETE | `/api/hub/{hub_id}/members/{id}` | JWT (owner) | Remove member |
| DELETE | `/api/hub/invitations/{id}` | JWT (owner) | Cancel pending invitation |

#### POST /api/hub/{hub_id}/invite

Request:
```json
{
  "email": "newuser@example.com",
  "role": "VIEWER"
}
```

Response (201):
```json
{
  "id": 4,
  "hub_id": 1,
  "hub_name": "Home",
  "email": "newuser@example.com",
  "token": "uuid-string",
  "status": "PENDING",
  "role": "VIEWER",
  "invited_by_email": "owner@example.com",
  "expires_at": "2026-06-03T11:20:00Z"
}
```

Errors: 400 (already invited/member), 403 (not owner), 429 (rate limit: 50/day/hub)

#### GET /api/hub/{hub_id}/members

Response (200):
```json
{
  "owner": {
    "id": 1, "user_id": 42, "user_email": "owner@example.com",
    "user_full_name": "John Owner", "role": "OWNER"
  },
  "members": [
    {"id": 2, "user_id": 43, "user_email": "jane@example.com",
     "user_full_name": "Jane", "role": "EDITOR"}
  ],
  "pending_invitations": [
    {"id": 3, "email": "pending@example.com", "status": "PENDING",
     "role": "VIEWER", "expires_at": "2026-06-03T09:00:00Z"}
  ]
}
```

#### POST /api/hub/invitations/{token}/accept

- If authenticated user matches invitation email: immediately accept, create HubMember
- If unauthenticated: return invitation details, prompt signup
- After signup with matching email: auto-accept on next call

### B.4 Email Service

**File**: `apps/hub/services.py` (new)

Uses existing SES infrastructure (`utils/send_email.py`). Creates invitation record, generates UUID token, sends email with:
- Hub name, inviter name
- Accept/Decline buttons (deep links: `jupyter://app/invitation/{token}?action=accept`)
- Expiration date (14 days)

### B.5 Celery Task: Expiry Cleanup

```python
@shared_task
def cleanup_expired_invitations():
    HubInvitation.objects.filter(
        status='PENDING', expires_at__lt=timezone.now()
    ).update(status='EXPIRED')
```

Add to CELERY_BEAT_SCHEDULE (daily at midnight).

### B.6 Permission Updates

Update all hub API views to check `HubMember` instead of `Hub.user == request.user`:

```python
def is_hub_owner(user, hub):
    return HubMember.objects.filter(hub=hub, user=user, role='OWNER').exists()

def is_hub_member(user, hub):
    return HubMember.objects.filter(hub=hub, user=user).exists()

def can_access_hub(user, hub, required_role='VIEWER'):
    role_hierarchy = {'OWNER': 3, 'EDITOR': 2, 'VIEWER': 1}
    try:
        member = HubMember.objects.get(hub=hub, user=user)
        return role_hierarchy[member.role] >= role_hierarchy[required_role]
    except HubMember.DoesNotExist:
        return False
```

---

## C. Flutter App Changes

### Current State

The app already has a `members` module with:
- `ManageMembersScreen`, `PendingInvitationsScreen`
- `InviteUserDialog`, `MembersBloc`
- `MembersRemoteDataSource` using `CloudDioBuilder`
- Models for `HubMember` and `HubInvitation`

### C.1 New: Accept Invitation Screen

**File**: `lib/src/modules/authentication/presentation/screens/accept_invitation_screen.dart`

Shown when user clicks invitation link in email. Two paths:
- Existing user (logged in): show hub name + inviter, "Accept" / "Decline" buttons
- New user: show signup form (name, email pre-filled, password) OR Apple/Google sign-in buttons, then auto-accept

### C.2 Deep Link Handling

**File**: `lib/main.dart` or app initialization

Handle `jupyter://app/invitation/{token}` deep links from email. Parse token, navigate to AcceptInvitationScreen.

Also handle universal links: `https://app.jupyter.com.au/invitation/{token}`

### C.3 Updated Screens

- **ManageMembersScreen**: Add role change dropdown, resend invitation, cancel invitation
- **PendingInvitationsScreen**: Add accept/decline action buttons, show expiration countdown
- **InvitationCard widget**: Hub name, inviter, role, expiry, accept/decline
- **MemberListTile widget**: Avatar/initial, name, email, role badge, remove button (for owner)

### C.4 Updated Bloc

Add events: `UpdateMemberRoleEvent`, `ResendInvitationEvent`, `CancelInvitationEvent`

### C.5 Route

Add `/acceptInvitation` route to `routes.dart` pointing to `AcceptInvitationScreen`

---

## D. Migration and Rollout Plan

### Phase 1: Backend (Week 1-2)
1. Create models + migrations
2. Data migration: existing Hub.user -> HubMember(OWNER)
3. Implement all 9 endpoints
4. Set up SES email template
5. Celery expiry task
6. Deploy to staging, test all flows

### Phase 2: Flutter (Week 2-3)
1. Accept invitation screen + deep links
2. Update manage members + pending invitations screens
3. Update bloc with new events
4. Test: invite, accept, decline, remove, role change
5. Test social auth + invitation flow

### Phase 3: Rollout (Week 3-4)
1. Deploy backend with feature flag
2. App update to TestFlight
3. Monitor error logs + email delivery
4. Iterate on UX

### Backward Compatibility
- `Hub.user` field kept for backward compat (original owner reference)
- All existing API responses unchanged
- Legacy code gradually migrates to `HubMember` queries
- Single-user hubs work exactly as before (owner is sole HubMember)

---

## E. Security Considerations

| Concern | Mitigation |
|---------|------------|
| Token guessing | UUID4 (128-bit entropy), 14-day expiry |
| Email spoofing | Verify authenticated user email matches invitation email |
| Rate limiting | 50 invitations/day/hub, 10 failed accepts/IP |
| Owner removal | Cannot remove last owner; cannot self-demote from owner |
| Duplicate invites | Unique constraint on (hub, email, status=PENDING) |
| Expired tokens | Celery daily cleanup + check on accept |
| Social auth mismatch | Match invitation by email regardless of auth method |

---

## F. Open Questions (Need Product Decisions)

1. **Role granularity**: Start with OWNER/VIEWER only, or include EDITOR from day one?
2. **Multi-hub**: Can a user be invited to multiple hubs? If yes, need "Switch Hub" UI.
3. **Camera-level permissions**: Should viewers see all cameras or only assigned ones? (Phase 2?)
4. **Activity logging**: Track which user made which change? (Compliance vs nice-to-have)
5. **Invitation resend**: Extend expiry on resend or keep original?
6. **Bulk invite**: Allow CSV upload of emails? (Phase 2)
7. **Notification for owner**: When invitation is accepted, notify owner via push?
8. **Hub-local auth**: Does the hub Django need to know about invited users for local API calls? Or all auth goes through cloud?

---

## G. Files to Create/Modify

### Backend (jupyter-backend)
| File | Action |
|------|--------|
| `apps/hub/models.py` | Add HubMember, HubInvitation |
| `apps/hub/serializers.py` | Add member/invitation serializers |
| `apps/hub/views.py` | Add 9 endpoints |
| `apps/hub/services.py` | New: invitation email service |
| `apps/hub/tasks.py` | New/update: expiry cleanup task |
| `apps/hub/urls.py` | Add new routes |
| `apps/hub/managers.py` | Update for multi-user queries |
| `apps/hub/authentication.py` | Add role-based permission checks |
| `jupyter/settings/common.py` | Add config constants |
| `apps/hub/migrations/` | 2 new migrations |

### Flutter (jupyter-app-rebuild)
| File | Action |
|------|--------|
| `lib/src/modules/authentication/presentation/screens/accept_invitation_screen.dart` | New |
| `lib/src/modules/members/presentation/screens/manage_members_screen.dart` | Update |
| `lib/src/modules/members/presentation/screens/pending_invitations_screen.dart` | Update |
| `lib/src/modules/members/presentation/blocs/members_bloc.dart` | Add events |
| `lib/src/modules/members/presentation/blocs/members_event.dart` | Add events |
| `lib/src/modules/members/domain/repositories/members_repository.dart` | Add methods |
| `lib/src/modules/members/data/repositories/members_repository_impl.dart` | Implement |
| `lib/src/modules/members/data/datasources/members_remote_datasource.dart` | Add calls |
| `lib/src/modules/members/presentation/widgets/invitation_card.dart` | Update |
| `lib/src/modules/members/presentation/widgets/member_list_tile.dart` | Update |
| `lib/src/modules/app/routes.dart` | Add acceptInvitation route |
| `lib/main.dart` | Deep link handling |

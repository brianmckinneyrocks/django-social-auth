from uuid import uuid4

from django.conf import settings

from social_auth.models import User
from social_auth.backends.pipeline import USERNAME, USERNAME_MAX_LENGTH, warn_setting
from social_auth.signals import socialauth_not_registered, \
                                socialauth_registered, \
                                pre_update


def get_username(details, user=None, *args, **kwargs):
    """Return an username for new user. Return current user username
    if user was given.
    """
    if user:
        return {'username': user.username}

    warn_setting('SOCIAL_AUTH_FORCE_RANDOM_USERNAME', 'get_username')
    # warn_setting('SOCIAL_AUTH_DEFAULT_USERNAME', 'get_username')
    warn_setting('SOCIAL_AUTH_UUID_LENGTH', 'get_username')
    # warn_setting('SOCIAL_AUTH_USERNAME_FIXER', 'get_username')

    if getattr(settings, 'SOCIAL_AUTH_FORCE_RANDOM_USERNAME', False):
        username = uuid4().get_hex()
    elif details.get(USERNAME):
        username = details[USERNAME]
    elif settings.hasattr('SOCIAL_AUTH_DEFAULT_USERNAME'):
        username = settings.SOCIAL_AUTH_DEFAULT_USERNAME
        if callable(username):
            username = username()
    else:
        username = uuid4().get_hex()

    uuid_lenght = getattr(settings, 'SOCIAL_AUTH_UUID_LENGTH', 16)
    username_fixer = getattr(settings, 'SOCIAL_AUTH_USERNAME_FIXER',
                             lambda u: u)

    short_username = username[:USERNAME_MAX_LENGTH - uuid_lenght]
    final_username = None

    while True:
        final_username = username_fixer(username)[:USERNAME_MAX_LENGTH]

        try:
            User.objects.get(username=final_username)
        except User.DoesNotExist:
            break
        else:
            # User with same username already exists, generate a unique
            # username for current user using username as base but adding
            # a unique hash at the end. Original username is cut to avoid
            # the field max_length.
            username = short_username + uuid4().get_hex()[:uuid_lenght]
    return {'username': final_username}


def create_user(backend, details, response, uid, username, user=None, *args,
                **kwargs):
    """Create user. Depends on get_username pipeline."""
    if user:
        return {'user': user}
    if not username:
        return None

    # warn_setting('SOCIAL_AUTH_CREATE_USERS', 'create_user')
    if not getattr(settings, 'SOCIAL_AUTH_CREATE_USERS', True):
        ################################################################
        #Added by brianmckinney to allow us to only invite manly users
        ###############################################################
        from invite.models import Invitation
        invite_key = kwargs['request'].session.get('invite', "").strip()
 
        if invite_key:
           try:
              invite = Invitation.objects.get(key=invite_key)              
              email = details.get('email')
              from invite.views import notify_admins, update_invite
              #Notify admins that we have a new user
              #notify_admins(username)
              #Update date accepted, invite status
              user = User.objects.create_user(username=username, email=email)
              update_invite(invite_key, username)
              return {
                 'user': user,
                 'is_new': True
              }
           except Invitation.DoesNotExist:
              pass                                        
        ############## End edit ###################################
        # Send signal for cases where tracking failed registering is useful.
        socialauth_not_registered.send(sender=backend.__class__,
                                       uid=uid,
                                       response=response,
                                       details=details)
        return None

    email = details.get('email')
    return {
        'user': User.objects.create_user(username=username, email=email),
        'is_new': True
    }


def update_user_details(backend, details, response, user, is_new=False, *args,
                        **kwargs):
    """Update user details using data from provider."""
    changed = False  # flag to track changes

    warn_setting('SOCIAL_AUTH_CHANGE_SIGNAL_ONLY', 'update_user_details')

    # check if values update should be left to signals handlers only
    if not getattr(settings, 'SOCIAL_AUTH_CHANGE_SIGNAL_ONLY', False):
        for name, value in details.items():
            # do not update username, it was already generated
            if name == USERNAME:
                continue
            if value and value != getattr(user, name, None):
                setattr(user, name, value)
                changed = True

    # Fire a pre-update signal sending current backend instance,
    # user instance (created or retrieved from database), service
    # response and processed details.
    #
    # Also fire socialauth_registered signal for newly registered
    # users.
    #
    # Signal handlers must return True or False to signal instance
    # changes. Send method returns a list of tuples with receiver
    # and it's response.
    signal_response = lambda receiver_response: receiver_response[1]
    signal_kwargs = {'sender': backend.__class__, 'user': user,
                     'response': response, 'details': details}

    changed |= any(filter(signal_response, pre_update.send(**signal_kwargs)))

    # Fire socialauth_registered signal on new user registration
    if is_new:
        changed |= any(filter(signal_response,
                              socialauth_registered.send(**signal_kwargs)))

    if changed:
        user.save()

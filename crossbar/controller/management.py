#####################################################################################
#
#  Copyright (C) Tavendo GmbH
#
#  Unless a separate license agreement exists between you and Tavendo GmbH (e.g. you
#  have purchased a commercial license), the license terms below apply.
#
#  Should you enter into a separate license agreement after having received a copy of
#  this software, then the terms of such license agreement replace the terms below at
#  the time at which such license agreement becomes effective.
#
#  In case a separate license agreement ends, and such agreement ends without being
#  replaced by another separate license agreement, the license terms below apply
#  from the time at which said agreement ends.
#
#  LICENSE TERMS
#
#  This program is free software: you can redistribute it and/or modify it under the
#  terms of the GNU Affero General Public License, version 3, as published by the
#  Free Software Foundation. This program is distributed in the hope that it will be
#  useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
#  See the GNU Affero General Public License Version 3 for more details.
#
#  You should have received a copy of the GNU Affero General Public license along
#  with this program. If not, see <http://www.gnu.org/licenses/agpl-3.0.en.html>.
#
#####################################################################################

from __future__ import absolute_import

from twisted.internet.defer import inlineCallbacks

from autobahn.wamp.types import SubscribeOptions, PublishOptions
from autobahn.twisted.wamp import ApplicationSession

from crossbar._logging import make_logger

__all__ = ('NodeManagementSession', 'NodeManagementBridgeSession')


class NodeManagementSession(ApplicationSession):

    """
    This session is used for any uplink CDC connection.
    """

    log = make_logger()

    def onJoin(self, details):
        self.log.info("Joined realm '{realm}' on uplink CDC router", realm=details.realm)
        self.config.extra['onready'].callback(self)


class NodeManagementBridgeSession(ApplicationSession):

    """
    The management bridge is a WAMP session that lives on the local management router,
    but has access to a 2nd WAMP session that lives on the uplink CDC router.

    The bridge is responsible for forwarding calls from CDC into the local node,
    and for forwarding events from the local node to CDC.
    """

    log = make_logger()

    def __init__(self, config, management_session):
        """

        :param config: Session configuration.
        :type config: instance of `autobahn.wamp.types.ComponentConfig`
        :param management_session: uplink session.
        :type management_session: instance of `autobahn.wamp.protocol.ApplicationSession`
        """
        ApplicationSession.__init__(self, config)
        self._management_session = management_session
        self._regs = {}

    @inlineCallbacks
    def onJoin(self, details):

        self.log.debug("Joined realm '{realm}' on node management router", realm=details.realm)

        @inlineCallbacks
        def on_event(*args, **kwargs):
            details = kwargs.pop('details')
            topic = u"cdc." + details.topic
            try:
                yield self._management_session.publish(topic, *args, options=PublishOptions(acknowledge=True), **kwargs)
            except Exception as e:
                self.log.error(e)
            else:
                self.log.debug("Forwarded event on topic '{topic}'", topic=topic)

        yield self.subscribe(on_event, u"crossbar.node", options=SubscribeOptions(match=u"prefix", details_arg="details"))

        # we use the WAMP meta API implemented by CB to get notified whenever a procedure is
        # registered/unregister on the node management router, setup a forwarding procedure
        # and register that on the uplink CDC router
        #
        @inlineCallbacks
        def on_registration_create(session_id, registration):
            uri = registration['uri']

            def forward_call(*args, **kwargs):
                return self.call(uri, *args, **kwargs)

            reg = yield self._management_session.register(forward_call, uri)
            self._regs[registration['id']] = reg

            self.log.debug("Forwarding procedure: {procedure}", procedure=reg.procedure)

        yield self.subscribe(on_registration_create, u'wamp.registration.on_create')

        @inlineCallbacks
        def on_registration_delete(session_id, registration_id):
            reg = self._regs.pop(registration_id, None)

            if reg:
                yield reg.unregister()
                self.log.debug("Removed forwarding of procedure {procedure}", procedure=reg.procedure)
            else:
                self.log.warn("Could not remove forwarding for unmapped registration_id {reg_id}", reg_id=registration_id)

        yield self.subscribe(on_registration_delete, u'wamp.registration.on_delete')

        self.log.info("Management bridge ready")

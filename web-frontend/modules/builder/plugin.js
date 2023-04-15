import en from '@baserow/modules/builder/locales/en.json'
import fr from '@baserow/modules/builder/locales/fr.json'
import nl from '@baserow/modules/builder/locales/nl.json'
import de from '@baserow/modules/builder/locales/de.json'
import es from '@baserow/modules/builder/locales/es.json'
import it from '@baserow/modules/builder/locales/it.json'
import pl from '@baserow/modules/builder/locales/pl.json'
import ja from '@baserow/modules/builder/locales/ja.json'
import {
  IntegrationsBuilderSettingsType,
  ThemeBuilderSettingsType,
} from '@baserow/modules/builder/builderSettingTypes'

import pageStore from '@baserow/modules/builder/store/page'
import elementStore from '@baserow/modules/builder/store/element'
import { registerRealtimeEvents } from '@baserow/modules/builder/realtime'
import {
  HeadingElementType,
  ParagraphElementType,
} from '@baserow/modules/builder/elementTypes'
import {
  DesktopDeviceType,
  SmartphoneDeviceType,
  TabletDeviceType,
} from '@baserow/modules/builder/deviceTypes'
import { DuplicatePageJobType } from '@baserow/modules/builder/jobTypes'
import { BuilderApplicationType } from '@baserow/modules/builder/applicationTypes'
import { PublicSiteErrorPageType } from '@baserow/modules/builder/errorPageTypes'
import {
  DataSourcesPageHeaderItemType,
  ElementsPageHeaderItemType,
  SettingsPageHeaderItemType,
  VariablesPageHeaderItemType,
} from '@baserow/modules/builder/pageHeaderItemTypes'
import {
  EventsPageSidePanelType,
  GeneralPageSidePanelType,
  VisibilityPageSidePanelType,
  StylePageSidePanelType,
} from '@baserow/modules/builder/pageSidePanelTypes'

export default (context) => {
  const { store, app, isDev } = context

  // Allow locale file hot reloading in dev
  if (isDev && app.i18n) {
    const { i18n } = app
    i18n.mergeLocaleMessage('en', en)
    i18n.mergeLocaleMessage('fr', fr)
    i18n.mergeLocaleMessage('nl', nl)
    i18n.mergeLocaleMessage('de', de)
    i18n.mergeLocaleMessage('es', es)
    i18n.mergeLocaleMessage('it', it)
    i18n.mergeLocaleMessage('pl', pl)
    i18n.mergeLocaleMessage('ja', ja)
  }

  registerRealtimeEvents(app.$realtime)

  store.registerModule('page', pageStore)
  store.registerModule('element', elementStore)

  app.$registry.registerNamespace('builderSettings')
  app.$registry.registerNamespace('element')
  app.$registry.registerNamespace('device')
  app.$registry.registerNamespace('pageHeaderItem')

  app.$registry.register('application', new BuilderApplicationType(context))
  app.$registry.register('job', new DuplicatePageJobType(context))

  app.$registry.register(
    'builderSettings',
    new IntegrationsBuilderSettingsType(context)
  )
  app.$registry.register(
    'builderSettings',
    new ThemeBuilderSettingsType(context)
  )

  app.$registry.register('errorPage', new PublicSiteErrorPageType(context))

  app.$registry.register('element', new HeadingElementType(context))
  app.$registry.register('element', new ParagraphElementType(context))

  app.$registry.register('device', new DesktopDeviceType(context))
  app.$registry.register('device', new TabletDeviceType(context))
  app.$registry.register('device', new SmartphoneDeviceType(context))

  app.$registry.register(
    'pageHeaderItem',
    new ElementsPageHeaderItemType(context)
  )
  app.$registry.register(
    'pageHeaderItem',
    new DataSourcesPageHeaderItemType(context)
  )
  app.$registry.register(
    'pageHeaderItem',
    new VariablesPageHeaderItemType(context)
  )
  app.$registry.register(
    'pageHeaderItem',
    new SettingsPageHeaderItemType(context)
  )
  app.$registry.register('pageSidePanel', new GeneralPageSidePanelType(context))
  app.$registry.register('pageSidePanel', new StylePageSidePanelType(context))
  app.$registry.register(
    'pageSidePanel',
    new VisibilityPageSidePanelType(context)
  )
  app.$registry.register('pageSidePanel', new EventsPageSidePanelType(context))
}

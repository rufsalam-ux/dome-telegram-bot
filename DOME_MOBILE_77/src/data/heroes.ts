export const presetHeroes = [
  {id:'robot', title:'Робот', image:require('../../assets/heroes/robot.png')},
  {id:'fox', title:'Лисёнок', image:require('../../assets/heroes/fox.png')},
  // The familiar DOME cat and the legacy gray cat are distinct server IDs.
  // Do not merge them by display name or asset path: existing child profiles
  // reference `cat`, whereas new DOME artwork is `dome_cat`.
  {id:'dome_cat', title:'Кот DOME', image:require('../../assets/heroes/cat.png')},
  {id:'cat', title:'Серый котик', image:require('../../assets/heroes/legacy-cat.png')},
  {id:'dragon', title:'Дракончик', image:require('../../assets/heroes/dragon.png')},
  {id:'explorer', title:'Путешественник', image:require('../../assets/heroes/explorer.png')},
  {id:'star', title:'Звёздный герой', image:require('../../assets/heroes/star.png')},
];
